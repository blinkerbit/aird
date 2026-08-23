import os
import sys
import shutil
import subprocess
import argparse
import re
import glob

# --- Configuration ---
PACKAGE_NAME = "aird"
SETUP_FILE = "setup.py"
SRC_CSS = "src/input.css"
DIST_CSS = "aird/static/css/app.css"
DEPLOY_HOST = os.environ.get("AIRD_DEPLOY_HOST", "ubuntu@u")
DEPLOY_DIR = os.environ.get("AIRD_DEPLOY_DIR", "/opt/aird")
DEPLOY_VENV = os.environ.get("AIRD_DEPLOY_VENV", "/opt/aird/.venv")
DEPLOY_SERVICE = os.environ.get("AIRD_DEPLOY_SERVICE", "aird")
DEPLOY_URL = os.environ.get("AIRD_DEPLOY_URL", "https://aird.pothukuchi.com")

# Safe CLI tokens for deploy/ssh targets (blocks argument injection).
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9._@%-]+$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/\\-]+$")
_SAFE_SERVICE_RE = re.compile(r"^[A-Za-z0-9._@:-]+$")
_SAFE_WHEEL_RE = re.compile(r"^[A-Za-z0-9._+-]+\.whl$")


def _require_safe(value: str, pattern: re.Pattern, label: str) -> str:
    text = str(value or "").strip()
    if not text or not pattern.fullmatch(text):
        print(f"Error: invalid {label}: {value!r}", file=sys.stderr)
        sys.exit(1)
    return text


def run_command(cmd):
    """Run an argv list; exit on failure."""
    if not isinstance(cmd, (list, tuple)) or not all(isinstance(c, str) for c in cmd):
        print("Error: run_command requires a list of string arguments", file=sys.stderr)
        sys.exit(1)
    try:
        subprocess.check_call(list(cmd))
    except subprocess.CalledProcessError as e:
        print(f"\nError: Command failed with exit code {e.returncode}")
        sys.exit(e.returncode)


def run_shell_command_checked(cmd):
    """Run an argv list without exiting; True on success."""
    if not isinstance(cmd, (list, tuple)) or not all(isinstance(c, str) for c in cmd):
        print(
            "Error: run_shell_command_checked requires a list of string arguments",
            file=sys.stderr,
        )
        return False
    try:
        subprocess.check_call(list(cmd))
        return True
    except subprocess.CalledProcessError as e:
        print(f"\nError: Command failed with exit code {e.returncode}")
        return False


def clean():
    """Clean up build and temporary files."""
    print("Cleaning up build artifacts...")
    folders = ["build", "dist", f"{PACKAGE_NAME}.egg-info", "htmlcov", ".pytest_cache"]
    for folder in folders:
        if os.path.exists(folder):
            shutil.rmtree(folder)

    if os.path.exists(".coverage"):
        os.remove(".coverage")

    # Clean __pycache__
    for root, dirs, files in os.walk("."):
        for d in dirs:
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d))


def build_css():
    """Compile Tailwind CSS."""
    if os.path.exists("package.json"):
        print("Building Tailwind CSS...")
        run_command(["npm", "install"])
        run_command(["npm", "run", "css:build"])
    else:
        print("Warning: package.json not found, skipping CSS build.")


def build_js():
    """Bundle share UI (esbuild output is not committed; see .gitignore)."""
    if os.path.exists("package.json"):
        print("Building share JS bundle...")
        run_command(["npm", "install"])
        run_command(["npm", "run", "js:share"])
    else:
        print("Warning: package.json not found, skipping JS bundle.")


def build():
    """Build the Python package binaries."""
    clean()
    build_css()
    build_js()
    if run_shell_command_checked(["uv", "build"]):
        return
    if (
        run_shell_command_checked([sys.executable, "-m", "pip", "install", "build"])
        and run_shell_command_checked([sys.executable, "-m", "build"])
    ):
        return
    if not run_shell_command_checked(
        [sys.executable, "-m", "pip", "install", "setuptools", "wheel"]
    ):
        sys.exit(1)
    run_command([sys.executable, SETUP_FILE, "sdist", "bdist_wheel"])


def install():
    """Build and install the package binaries."""
    build()
    print("Installing package...")
    wheels = [f for f in os.listdir("dist") if f.endswith(".whl")]
    if not wheels:
        print("Error: No wheel found in dist/ after build.")
        sys.exit(1)
    wheel_path = os.path.join("dist", wheels[0])
    if run_shell_command_checked(
        ["uv", "pip", "install", wheel_path, "--force-reinstall"]
    ):
        return
    run_command(
        [sys.executable, "-m", "pip", "install", wheel_path, "--force-reinstall"]
    )


def test(verbose=False, quick=False):
    """Run tests using pytest."""
    cmd = [sys.executable, "-m", "pytest", "tests/", "-n", "auto"]
    if quick:
        cmd.extend(["-q", "--no-header"])
    elif verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")
    run_command(cmd)


def lint():
    """Run linting tests."""
    if os.path.exists("run_tests.py"):
        run_command([sys.executable, "run_tests.py", "--lint"])
    else:
        print("Error: run_tests.py not found.")


_VERSION_RE = re.compile(
    r'version="(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<suffix>(?:\.dev\d+|rc\d+)?)"'
)


def _read_version():
    with open(SETUP_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    match = _VERSION_RE.search(content)
    if not match:
        print("Error: Could not find version string in setup.py")
        sys.exit(1)
    return content, match


def _write_version(content, new_version):
    new_content = _VERSION_RE.sub(f'version="{new_version}"', content, count=1)
    with open(SETUP_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"Version set to: {new_version}")
    return new_version


def bump_version(part="patch"):
    """Increment the version in setup.py (stable release)."""
    content, match = _read_version()
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1
    return _write_version(content, f"{major}.{minor}.{patch}")


def bump_dev_version():
    """Bump to next dev release (e.g. 0.4.22 -> 0.4.23.dev0, 0.4.23.dev0 -> 0.4.23.dev1)."""
    content, match = _read_version()
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    suffix = match.group("suffix") or ""
    dev_match = re.fullmatch(r"\.dev(\d+)", suffix)
    if dev_match:
        dev_num = int(dev_match.group(1)) + 1
    else:
        patch += 1
        dev_num = 0
    return _write_version(content, f"{major}.{minor}.{patch}.dev{dev_num}")


def _find_wheel():
    if not os.path.isdir("dist"):
        print("Error: dist/ not found. Run build first.")
        sys.exit(1)
    wheels = sorted(
        [f for f in os.listdir("dist") if f.endswith(".whl")],
        key=lambda name: os.path.getmtime(os.path.join("dist", name)),
        reverse=True,
    )
    if not wheels:
        print("Error: No wheel found in dist/ after build.")
        sys.exit(1)
    return wheels[0]


def deploy_wheel(host=None, deploy_dir=None, venv=None, service=None, wheel=None):
    """Upload wheel via scp, install into remote venv, restart systemd service."""
    host = _require_safe(host or DEPLOY_HOST, _SAFE_HOST_RE, "deploy host")
    deploy_dir = _require_safe(deploy_dir or DEPLOY_DIR, _SAFE_PATH_RE, "deploy dir")
    venv = _require_safe(venv or DEPLOY_VENV, _SAFE_PATH_RE, "deploy venv")
    service = _require_safe(service or DEPLOY_SERVICE, _SAFE_SERVICE_RE, "deploy service")
    wheel = _require_safe(wheel or _find_wheel(), _SAFE_WHEEL_RE, "wheel name")

    local_wheel = os.path.join("dist", wheel)
    remote_wheel = f"{deploy_dir}/{wheel}"

    print(f"Uploading {local_wheel} -> {host}:{deploy_dir}/")
    run_command(["scp", local_wheel, f"{host}:{deploy_dir}/"])

    remote_cmd = (
        f"uv pip install --python {venv}/bin/python --prerelease=allow "
        f"--force-reinstall {remote_wheel} "
        f"&& sudo systemctl restart {service}"
    )
    print(f"Installing on {host} and restarting {service}...")
    run_command(["ssh", host, remote_cmd])
    print(f"Deployed {wheel}. Live at {DEPLOY_URL}")


def _upload_dist(version, prerelease=False):
    print(f"\nReady to upload version {version} to PyPI?")
    if prerelease:
        print("Install with (pip):")
        print(f"  pip install --pre aird=={version}")
        print("Install with (uv):")
        print(f'  uv pip install --prerelease=allow "aird=={version}"')
        print("  # or: uv pip install " + f'"aird>={version}"')
    confirm = input("Type 'yes' to proceed with twine upload: ")
    if confirm.lower() == "yes":
        for artifact in glob.glob("dist/*"):
            run_command([sys.executable, "-m", "twine", "upload", artifact])
    else:
        print("Upload cancelled.")


def release(part="patch"):
    """Handle full stable release process."""
    version = bump_version(part)
    build()
    _upload_dist(version)


def release_dev(do_deploy=False):
    """Bump .devN, build wheel (uv build), then upload to PyPI or deploy to server."""
    version = bump_dev_version()
    build()
    if do_deploy:
        deploy_wheel()
    else:
        _upload_dist(version, prerelease=True)


def deploy_dev():
    """Build current tree and deploy wheel to the configured production host."""
    build()
    deploy_wheel()


def main():
    parser = argparse.ArgumentParser(description="Aird Management Script")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("clean", help="Clean build artifacts")
    subparsers.add_parser("build", help="Compile CSS and build binaries")
    subparsers.add_parser("install", help="Build and install the package")
    subparsers.add_parser("lint", help="Run linting")

    test_parser = subparsers.add_parser("test", help="Run tests")
    test_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    test_parser.add_argument("--quick", action="store_true", help="Quick run")

    release_parser = subparsers.add_parser("release", help="Bump version and publish stable")
    release_parser.add_argument(
        "--patch", action="store_const", const="patch", dest="part", default="patch"
    )
    release_parser.add_argument("--minor", action="store_const", const="minor", dest="part")
    release_parser.add_argument("--major", action="store_const", const="major", dest="part")

    release_dev_parser = subparsers.add_parser(
        "release-dev",
        help="Bump .devN version, build with uv, upload to PyPI or deploy to server",
    )
    release_dev_parser.add_argument(
        "--deploy",
        action="store_true",
        help=f"Deploy wheel to {DEPLOY_HOST}:{DEPLOY_DIR} instead of PyPI upload",
    )

    deploy_parser = subparsers.add_parser(
        "deploy",
        help=f"Build and deploy wheel to {DEPLOY_HOST}:{DEPLOY_DIR}",
    )
    deploy_parser.add_argument("--host", default=DEPLOY_HOST, help="SSH target (user@host)")
    deploy_parser.add_argument("--dir", default=DEPLOY_DIR, help="Remote install directory")
    deploy_parser.add_argument("--venv", default=DEPLOY_VENV, help="Remote virtualenv path")
    deploy_parser.add_argument("--service", default=DEPLOY_SERVICE, help="systemd unit name")

    args = parser.parse_args()

    if args.command == "clean":
        clean()
    elif args.command == "build":
        build()
    elif args.command == "install":
        install()
    elif args.command == "lint":
        lint()
    elif args.command == "test":
        test(verbose=args.verbose, quick=args.quick)
    elif args.command == "release":
        release(args.part)
    elif args.command == "release-dev":
        release_dev(do_deploy=args.deploy)
    elif args.command == "deploy":
        build()
        deploy_wheel(
            host=args.host, deploy_dir=args.dir, venv=args.venv, service=args.service
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
