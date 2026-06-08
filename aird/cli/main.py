"""aird-cli entry point."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

from aird.cli import __version__
from aird.cli.authelia import AutheliaError, login as authelia_login, second_factor
from aird.cli.config import (
    get_authelia_url,
    get_parallel_jobs,
    get_server_url,
    load_config,
    save_config,
)
from aird.cli.session import AirdAPIError, AirdAuthError, AirdClient

log = logging.getLogger(__name__)


def _prompt_password(prompt: str = "Password: ") -> str:
    return getpass.getpass(prompt)


def _prompt_totp() -> str:
    return input("One-time code (TOTP): ").strip()


def cmd_config_set(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg[args.key] = args.value
    save_config(cfg)
    print(f"Set {args.key} = {args.value}")
    return 0


def cmd_config_show(_args: argparse.Namespace) -> int:
    cfg = load_config()
    if not cfg:
        print("(no config — use: aird-cli config set server https://host)")
        return 0
    for k, v in sorted(cfg.items()):
        if k == "password":
            print(f"{k}: ***")
        else:
            print(f"{k}: {v}")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    server = get_server_url()
    if not server and args.server:
        cfg = load_config()
        cfg["server"] = args.server.rstrip("/")
        save_config(cfg)
        server = cfg["server"]
    if not server:
        print("Set server URL first: aird-cli config set server https://your-host", file=sys.stderr)
        return 1

    client = AirdClient(server)
    authelia_url = get_authelia_url() or args.authelia_url

    username = args.username or load_config().get("username") or input("Username: ").strip()
    if not username:
        print("Username required", file=sys.stderr)
        return 1

    if args.token:
        client.set_bearer_token(args.token)
        client.refresh_xsrf()
        client.save()
        try:
            client.check_auth()
            print(f"Logged in to {server} (access token)")
            return 0
        except (AirdAuthError, AirdAPIError) as exc:
            print(f"Token login failed: {exc}", file=sys.stderr)
            return 1

    password = args.password or _prompt_password()
    totp = args.totp

    if authelia_url:
        try:
            authelia_login(
                client.http,
                authelia_url,
                username,
                password,
                totp=totp,
                target_url=f"{server}/login",
            )
        except AutheliaError as exc:
            if str(exc) == "second_factor_required":
                totp = totp or _prompt_totp()
                try:
                    second_factor(client.http, authelia_url, totp)
                except AutheliaError as exc2:
                    print(f"Authelia login failed: {exc2}", file=sys.stderr)
                    return 1
            else:
                print(f"Authelia login failed: {exc}", file=sys.stderr)
                return 1

    aird_token = args.aird_token
    if aird_token:
        client.login_password("", "", token=aird_token)
    else:
        client.login_password(username, password)

    cfg = load_config()
    cfg["username"] = username
    cfg["server"] = server
    if authelia_url:
        cfg["authelia_url"] = authelia_url
    save_config(cfg)
    client.save()

    try:
        client.check_auth()
    except (AirdAuthError, AirdAPIError) as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1

    print(f"Logged in to {server} as {username}")
    return 0


def cmd_logout(_args: argparse.Namespace) -> int:
    try:
        client = AirdClient()
        client.clear_session()
    except AirdAuthError:
        pass
    print("Session cleared")
    return 0


def _default_jobs(args: argparse.Namespace) -> int:
    jobs = getattr(args, "jobs", None)
    return jobs if jobs is not None else get_parallel_jobs()


def cmd_whoami(_args: argparse.Namespace) -> int:
    try:
        client = AirdClient()
        client.check_auth()
        username = client.http.cookies.get("user") or load_config().get("username") or "?"
        print(f"OK — {username} @ {client.server}")
        return 0
    except AirdAuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except AirdAPIError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_ls(args: argparse.Namespace) -> int:
    try:
        client = AirdClient()
        entries = client.list_dir(args.path or "")
        if not entries:
            print("(empty)")
            return 0
        for e in entries:
            name = e.get("name", "?")
            if e.get("is_dir"):
                print(f"{name}/")
            else:
                size = e.get("size_bytes") or e.get("size") or 0
                print(f"{name}\t{size}")
        return 0
    except (AirdAuthError, AirdAPIError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_download(args: argparse.Namespace) -> int:
    try:
        client = AirdClient()
        remote = (args.remote or "").strip("/")
        local = Path(args.output or ".").resolve()

        if args.recursive:
            base_name = Path(remote).name if remote else "files"
            dest = (local / base_name) if local.is_dir() else local
            dest.mkdir(parents=True, exist_ok=True)

            def on_progress(p: str) -> None:
                if args.verbose:
                    print(p)

            n = client.download_tree(
                remote,
                dest,
                workers=_default_jobs(args),
                on_progress=on_progress if args.verbose else None,
            )
            print(f"Downloaded {n} file(s) to {dest}")
            return 0

        if not remote:
            print("Remote path required", file=sys.stderr)
            return 1
        dest = local
        if dest.is_dir():
            dest = dest / Path(remote).name
        client.download_file(remote, dest)
        print(f"Saved {dest}")
        return 0
    except (AirdAuthError, AirdAPIError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_upload(args: argparse.Namespace) -> int:
    try:
        client = AirdClient()
        local = Path(args.local).resolve()
        remote_dir = (args.remote or "").strip("/")

        def on_progress(p: str) -> None:
            if args.verbose:
                print(p)

        if local.is_dir():
            n = client.upload_tree(
                local,
                remote_dir,
                workers=_default_jobs(args),
                on_progress=on_progress if args.verbose else None,
            )
            print(f"Uploaded {n} file(s) to /{remote_dir or ''}")
        elif local.is_file():
            client.upload_file(local, remote_dir)
            print(f"Uploaded {local.name} to /{remote_dir or ''}")
            n = 1
        else:
            print(f"Not found: {local}", file=sys.stderr)
            return 1
        return 0
    except (AirdAuthError, AirdAPIError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_shares_list(_args: argparse.Namespace) -> int:
    try:
        client = AirdClient()
        data = client.list_shares()
        mine = data.get("shares") or []
        shared = data.get("shared_with_me") or []
        if not mine and not shared:
            print("(no shares)")
            return 0
        if mine:
            print("My shares:")
            items = mine.values() if isinstance(mine, dict) else mine
            for s in items:
                sid = s.get("id", "?")
                paths = s.get("paths") or []
                print(f"  {sid}\t{len(paths)} path(s)\t{s.get('created_at', '')}")
        if shared:
            print("Shared with me:")
            for s in shared:
                sid = s.get("id", "?")
                creator = s.get("creator") or s.get("username") or "?"
                paths = s.get("paths") or []
                print(f"  {sid}\tfrom {creator}\t{len(paths)} path(s)")
        return 0
    except (AirdAuthError, AirdAPIError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_shares_download(args: argparse.Namespace) -> int:
    try:
        client = AirdClient()
        dest = Path(args.output or ".").resolve() / args.share_id
        dest.mkdir(parents=True, exist_ok=True)
        token = args.token

        def on_progress(p: str) -> None:
            if args.verbose:
                print(p)

        n = client.download_share(
            args.share_id,
            dest,
            share_token=token,
            workers=_default_jobs(args),
            on_progress=on_progress if args.verbose else None,
        )
        print(f"Downloaded {n} file(s) from share {args.share_id} to {dest}")
        return 0
    except (AirdAuthError, AirdAPIError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_shares_download_all(args: argparse.Namespace) -> int:
    try:
        client = AirdClient()
        dest = Path(args.output or ".").resolve()

        def on_progress(p: str) -> None:
            if args.verbose:
                print(p)

        n = client.download_all_shares(
            dest,
            workers=_default_jobs(args),
            on_progress=on_progress if args.verbose else None,
        )
        print(f"Downloaded {n} file(s) from all shares to {dest}")
        return 0
    except (AirdAuthError, AirdAPIError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aird-cli",
        description="Aird CLI — upload, download, and shares (session saved after login)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="Progress per file")
    sub = p.add_subparsers(dest="command", required=True)

    cfg = sub.add_parser("config", help="Manage CLI config")
    cfg_sub = cfg.add_subparsers(dest="config_cmd", required=True)
    cs = cfg_sub.add_parser("set", help="Set config key")
    cs.add_argument(
        "key",
        choices=["server", "authelia_url", "username", "parallel_uploads", "parallel_downloads"],
    )
    cs.add_argument("value")
    cs.set_defaults(func=cmd_config_set)
    cfg_sub.add_parser("show", help="Show config").set_defaults(func=cmd_config_show)

    login = sub.add_parser("login", help="Authenticate (Authelia + Aird); session persisted")
    login.add_argument("--server", help="Aird base URL")
    login.add_argument("--authelia-url", dest="authelia_url", help="Authelia portal URL")
    login.add_argument("-u", "--username")
    login.add_argument("-p", "--password", help="Password (avoid on shell history)")
    login.add_argument("--totp", help="Authelia TOTP code")
    login.add_argument(
        "--token",
        help="Aird ACCESS_TOKEN (Bearer); skips username/password",
    )
    login.add_argument(
        "--aird-token",
        dest="aird_token",
        help="Aird login form token field (not Bearer)",
    )
    login.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="Clear saved session").set_defaults(func=cmd_logout)
    sub.add_parser("whoami", help="Check current session").set_defaults(func=cmd_whoami)

    ls = sub.add_parser("ls", help="List remote directory")
    ls.add_argument("path", nargs="?", default="", help="Remote path under your files root")
    ls.set_defaults(func=cmd_ls)

    dl = sub.add_parser("download", help="Download file or folder (no zip)")
    dl.add_argument("remote", help="Remote path")
    dl.add_argument("-o", "--output", help="Local file or directory")
    dl.add_argument("-r", "--recursive", action="store_true", help="Download folder tree")
    dl.add_argument("-j", "--jobs", type=int, default=None, help="Parallel downloads (default from config)")
    dl.set_defaults(func=cmd_download)

    up = sub.add_parser("upload", help="Upload file or folder")
    up.add_argument("local", help="Local file or directory")
    up.add_argument("remote", nargs="?", default="", help="Remote destination directory")
    up.add_argument("-j", "--jobs", type=int, default=None, help="Parallel file uploads (default from config)")
    up.set_defaults(func=cmd_upload)

    shares = sub.add_parser("shares", help="Share operations")
    sh_sub = shares.add_subparsers(dest="shares_cmd", required=True)
    sh_sub.add_parser("list", help="List shares").set_defaults(func=cmd_shares_list)
    sh_dl = sh_sub.add_parser("download", help="Download all files in a share")
    sh_dl.add_argument("share_id")
    sh_dl.add_argument("-o", "--output", help="Local parent directory")
    sh_dl.add_argument("--token", help="Share access token if required")
    sh_dl.add_argument("-j", "--jobs", type=int, default=None)
    sh_dl.set_defaults(func=cmd_shares_download)
    sh_all = sh_sub.add_parser("download-all", help="Download every share you can access")
    sh_all.add_argument("-o", "--output", help="Local parent directory")
    sh_all.add_argument("-j", "--jobs", type=int, default=None)
    sh_all.set_defaults(func=cmd_shares_download_all)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
