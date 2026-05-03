import os
import sys
import shutil
import subprocess
import argparse
import re

# --- Configuration ---
PACKAGE_NAME = "aird"
SETUP_FILE = "setup.py"
SRC_CSS = "src/input.css"
DIST_CSS = "aird/static/css/app.css"

def run_command(cmd, shell=True):
    """Utility to run a command and exit on failure."""
    try:
        subprocess.check_call(cmd, shell=shell)
    except subprocess.CalledProcessError as e:
        print(f"\nError: Command failed with exit code {e.returncode}")
        sys.exit(e.returncode)

def clean():
    """Clean up build and temporary files."""
    print("Cleaning up build artifacts...")
    folders = ['build', 'dist', f'{PACKAGE_NAME}.egg-info', 'htmlcov', '.pytest_cache']
    for folder in folders:
        if os.path.exists(folder):
            shutil.rmtree(folder)
    
    if os.path.exists('.coverage'):
        os.remove('.coverage')
    
    # Clean __pycache__
    for root, dirs, files in os.walk('.'):
        for d in dirs:
            if d == '__pycache__':
                shutil.rmtree(os.path.join(root, d))

def build_css():
    """Compile Tailwind CSS."""
    if os.path.exists("package.json"):
        print("Building Tailwind CSS...")
        run_command("npm install")
        run_command("npm run css:build")
    else:
        print("Warning: package.json not found, skipping CSS build.")

def build():
    """Build the Python package binaries."""
    clean()
    build_css()
    try:
        run_command("uv build")
    except SystemExit:
        try:
            # Fallback to modern Python build package
            run_command(f"{sys.executable} -m pip install build")
            run_command(f"{sys.executable} -m build")
        except SystemExit:
            # Final fallback to setup.py
            run_command(f"{sys.executable} -m pip install setuptools wheel")
            run_command(f"{sys.executable} {SETUP_FILE} sdist bdist_wheel")

def install():
    """Build and install the package binaries."""
    build()
    print("Installing package...")
    # Find the wheel in dist/
    wheels = [f for f in os.listdir('dist') if f.endswith('.whl')]
    if not wheels:
        print("Error: No wheel found in dist/ after build.")
        sys.exit(1)
    try:
        run_command(f"uv pip install dist/{wheels[0]} --force-reinstall")
    except SystemExit:
        run_command(f"{sys.executable} -m pip install dist/{wheels[0]} --force-reinstall")

def test(verbose=False, quick=False):
    """Run tests using pytest."""
    cmd = [sys.executable, "-m", "pytest", "tests/", "-n", "auto"]
    if quick:
        cmd.extend(["-q", "--no-header"])
    elif verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")
    run_command(" ".join(cmd))

def lint():
    """Run linting tests."""
    if os.path.exists("run_tests.py"):
        run_command(f"{sys.executable} run_tests.py --lint")
    else:
        print("Error: run_tests.py not found.")

def bump_version(part='patch'):
    """Increment the version in setup.py."""
    with open(SETUP_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    match = re.search(r'version="(\d+)\.(\d+)\.(\d+)"', content)
    if not match:
        print("Error: Could not find version string in setup.py")
        sys.exit(1)

    major, minor, patch = map(int, match.groups())
    if part == 'major': major += 1; minor = 0; patch = 0
    elif part == 'minor': minor += 1; patch = 0
    else: patch += 1

    new_version = f"{major}.{minor}.{patch}"
    new_content = re.sub(r'version="\d+\.\d+\.\d+"', f'version="{new_version}"', content)
    
    with open(SETUP_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"Bumped version to: {new_version}")
    return new_version

def release(part='patch'):
    """Handle full release process."""
    version = bump_version(part)
    build()
    print(f"\nReady to upload version {version} to PyPI?")
    confirm = input("Type 'yes' to proceed with twine upload: ")
    if confirm.lower() == 'yes':
        run_command("twine upload dist/*")
    else:
        print("Upload cancelled.")

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

    release_parser = subparsers.add_parser("release", help="Bump version and publish")
    release_parser.add_argument("--patch", action="store_const", const="patch", dest="part", default="patch")
    release_parser.add_argument("--minor", action="store_const", const="minor", dest="part")
    release_parser.add_argument("--major", action="store_const", const="major", dest="part")

    args = parser.parse_args()

    if args.command == "clean": clean()
    elif args.command == "build": build()
    elif args.command == "install": install()
    elif args.command == "lint": lint()
    elif args.command == "test": test(verbose=args.verbose, quick=args.quick)
    elif args.command == "release": release(args.part)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
