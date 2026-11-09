"""phonehome-server command line.

  phonehome-server serve              run the web app (and the job worker)
  phonehome-server worker             run only the job worker
  phonehome-server adduser NAME       create an account or change its password
  phonehome-server deluser NAME
  phonehome-server reindex            rebuild the database from the archive folder
  phonehome-server verify             check every file against SHA256SUMS
"""

from __future__ import annotations

import argparse
import getpass
import logging
import signal
import sys
import threading

from . import __version__
from .config import load_settings


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="phonehome-server", description="PhoneHome server")
    p.add_argument("--config", help="path to server.toml (default: $PHONEHOME_CONFIG or /etc/phonehome/server.toml)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    sub.add_parser("worker", help="run only the background job worker")
    a = sub.add_parser("adduser", help="create an account or change its password")
    a.add_argument("username")
    a.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    d = sub.add_parser("deluser", help="delete an account")
    d.add_argument("username")
    sub.add_parser("reindex", help="rebuild the database index from the archive folder")
    sub.add_parser("verify", help="check archive files against SHA256SUMS")
    args = p.parse_args(argv)

    settings = load_settings(args.config)
    _setup_logging(settings.log_level)

    if args.cmd == "serve":
        import uvicorn

        from .app import create_app

        app = create_app(settings)
        uvicorn.run(app, host=args.host or settings.host, port=args.port or settings.port,
                    log_level=settings.log_level.lower(), proxy_headers=True, forwarded_allow_ips="*")
        return 0

    from .app import Services

    svc = Services(settings)

    if args.cmd == "worker":
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        svc.worker.start()
        stop.wait()
        svc.worker.stop()
        return 0

    if args.cmd == "adduser":
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass(f"Password for {args.username}: ")
            if password != getpass.getpass("Again: "):
                print("Passwords don't match.", file=sys.stderr)
                return 1
        try:
            svc.auth.set_user(args.username, password)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Account {args.username!r} saved.")
        return 0

    if args.cmd == "deluser":
        if not svc.auth.delete_user(args.username):
            print(f"No account {args.username!r}.", file=sys.stderr)
            return 1
        print(f"Account {args.username!r} deleted.")
        return 0

    if args.cmd == "reindex":
        counts = svc.messages.reindex()
        print(", ".join(f"{k}: {v}" for k, v in counts.items()))
        if counts["mp3_queued"]:
            print("Making missing MP3 copies...")
            svc.worker.run_all()
        return 1 if counts["errors"] else 0

    if args.cmd == "verify":
        from .archive import sha256_file

        sums = svc.archive.read_sums()
        bad = missing = 0
        for rel, digest in sums.items():
            path = svc.archive.root / rel
            if not path.exists():
                print(f"MISSING  {rel}")
                missing += 1
            elif sha256_file(path) != digest:
                print(f"CHANGED  {rel}")
                bad += 1
        print(f"{len(sums)} files checked, {bad} changed, {missing} missing.")
        return 1 if bad or missing else 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
