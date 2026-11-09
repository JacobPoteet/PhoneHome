"""phonehome-server command line.

  phonehome-server serve              run the web app (and the job worker)
  phonehome-server worker             run only the job worker
  phonehome-server adduser NAME       create an account or change its password
  phonehome-server deluser NAME
  phonehome-server reindex            rebuild the database from the archive folder
  phonehome-server verify             check every file against SHA256SUMS
  phonehome-server transcribe [ID]    transcribe one message, or all without a transcript
  phonehome-server viewer             rebuild the archive's offline index.html
  phonehome-server export FILE.zip    write the whole archive (+ offline viewer) to a zip
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
    sub.add_parser("viewer", help="rebuild the archive's offline index.html")
    e = sub.add_parser("export", help="write the whole archive to a zip file")
    e.add_argument("file")
    e.add_argument("--no-mp3", action="store_true", help="leave out the MP3 copies (smaller)")
    e.add_argument("--trash", action="store_true", help="include the trash")
    t = sub.add_parser("transcribe", help="transcribe messages now (all missing ones, or one ID)")
    t.add_argument("id", nargs="?")
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
        if counts["mp3_queued"] or counts["transcribe_queued"]:
            print("Making missing MP3 copies and transcripts (Ctrl+C to leave them to the server)...")
            svc.worker.run_all()
        return 1 if counts["errors"] else 0

    if args.cmd == "transcribe":
        if svc.transcriber is None:
            print("Transcription is unavailable: install faster-whisper "
                  "(pip install 'phonehome-server[transcribe]') and keep PHONEHOME_TRANSCRIBE on.", file=sys.stderr)
            return 1
        n = svc.messages.queue_transcription(args.id)
        print(f"Transcribing {n} message(s) with {svc.transcriber.name}...")
        svc.worker.run_all()
        return 0

    if args.cmd == "viewer":
        from .viewer import write_viewer

        print(f"Wrote {write_viewer(svc.archive)}")
        return 0

    if args.cmd == "export":
        from .backup import export_stream, kv_set

        size = 0
        with open(args.file, "wb") as fh:
            for chunk in export_stream(svc.archive, include_mp3=not args.no_mp3, include_trash=args.trash,
                                       on_done=lambda info: kv_set(svc.db, "last_export", info)):
                fh.write(chunk)
                size += len(chunk)
        print(f"Wrote {args.file} ({size / 1048576:.1f} MB)")
        return 0

    if args.cmd == "verify":
        from .backup import integrity_check, kv_set

        r = integrity_check(svc.archive)
        kv_set(svc.db, "integrity", r)  # shows on the Backup page
        for label, key in (("CHANGED  ", "changed"), ("MISSING  ", "missing"), ("NOT LISTED ", "untracked")):
            for rel in r[key]:
                print(label + rel)
        c = r["counts"]
        print(f"{r['files']} files checked, {c['changed']} changed, {c['missing']} missing, "
              f"{c['untracked']} not listed in SHA256SUMS ({r['seconds']} s).")
        return 0 if r["ok"] else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
