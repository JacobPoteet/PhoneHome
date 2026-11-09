"""Phase 7: export, offline viewer, integrity checks, backup reports."""

import hashlib
import io
import json
import zipfile

from conftest import CSRF, FakeTranscriber, flac_bytes, upload

MID = "2026-11-06_193210_k3f9"
MID2 = "2025-12-25_090000_abcd"


def seed(client, headers, svc):
    upload(client, headers)
    upload(client, headers, msg_id=MID2, audio=flac_bytes(freq=660))
    svc.worker.run_all()
    svc.messages.update(MID, {"title": "Grandma <visits> & stays", "people": ["Grandma"], "tags": ["thanksgiving"],
                              "notes": "</script><script>alert(1)</script>"})
    svc.messages.edit_transcript(MID2, "Merry Christmas from the cousins")
    svc.worker.run_all()


def test_viewer_is_written_to_the_archive_and_kept_current(logged_in, svc, agent_headers):
    seed(logged_in, agent_headers, svc)
    page = (svc.archive.root / "index.html").read_text()
    assert "2 messages" in page
    assert f'src="messages/2026/11/{MID}.mp3"' in page              # relative: works from a USB stick
    assert f'href="messages/2025/12/{MID2}.flac"' in page
    assert "Grandma &lt;visits&gt; &amp; stays" in page              # escaped
    assert "<script>alert(1)" not in page
    assert "Merry Christmas from the cousins" in page                # the human-corrected transcript
    assert page.index(MID) < page.index(MID2)                        # newest first
    assert 'id="y2026"' in page and 'id="y2025"' in page
    assert "http://" not in page and "https://" not in page          # nothing external

    svc.messages.trash(MID2)
    svc.worker.run_all()
    page = (svc.archive.root / "index.html").read_text()
    assert "1 message " in page and MID2 not in page


def test_export_zip_has_everything_and_matches_checksums(logged_in, svc, agent_headers):
    seed(logged_in, agent_headers, svc)
    svc.messages.trash(MID2)
    r = logged_in.get("/api/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="phonehome-archive-' in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.testzip() is None
    names = zf.namelist()
    root = names[0].split("/")[0]
    rel = {n[len(root) + 1:] for n in names}
    assert {"index.html", "README.txt", "SHA256SUMS", f"messages/2026/11/{MID}.flac", f"messages/2026/11/{MID}.mp3",
            f"messages/2026/11/{MID}.json", f"messages/2026/11/{MID}.txt", "greetings/builtin-chime.json"} <= rel
    assert not any(n.startswith(".trash") or "/." in "/" + n for n in rel)  # no trash, locks or temp files
    # Audio stored as-is, and every listed checksum matches what's in the zip.
    assert zf.getinfo(f"{root}/messages/2026/11/{MID}.flac").compress_type == zipfile.ZIP_STORED
    for line in zf.read(f"{root}/SHA256SUMS").decode().splitlines():
        digest, path = line.split("  ", 1)
        if path.startswith(".trash"):
            continue
        assert hashlib.sha256(zf.read(f"{root}/{path}")).hexdigest() == digest
    # The export is remembered.
    b = logged_in.get("/api/backup").json()
    assert b["last_export"]["files"] == len(names) and b["last_any"] == b["last_export"]["at"]


def test_export_options(logged_in, svc, agent_headers):
    seed(logged_in, agent_headers, svc)
    svc.messages.trash(MID2)
    names = zipfile.ZipFile(io.BytesIO(logged_in.get("/api/export?mp3=false&trash=true").content)).namelist()
    assert not any(n.endswith(".mp3") for n in names)
    assert any(f".trash/messages/2025/12/{MID2}.flac" in n for n in names)


def test_cli_export_and_viewer(logged_in, svc, agent_headers, settings, monkeypatch, tmp_path):
    from phonehome_server.cli import main

    seed(logged_in, agent_headers, svc)
    monkeypatch.setenv("PHONEHOME_ARCHIVE_DIR", str(settings.archive_dir))
    monkeypatch.setenv("PHONEHOME_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("PHONEHOME_CONFIG", "/nonexistent.toml")
    monkeypatch.setenv("PHONEHOME_TRANSCRIBE", "false")
    out = tmp_path / "backup.zip"
    assert main(["export", str(out), "--no-mp3"]) == 0
    assert zipfile.ZipFile(out).testzip() is None
    (svc.archive.root / "index.html").unlink()
    assert main(["viewer"]) == 0
    assert (svc.archive.root / "index.html").exists()


def test_integrity_check_finds_changed_missing_and_unlisted(logged_in, svc, agent_headers):
    from phonehome_server.backup import integrity_check

    seed(logged_in, agent_headers, svc)
    r = integrity_check(svc.archive)
    assert r["ok"] and r["files"] == 4 and r["counts"] == {"changed": 0, "missing": 0, "untracked": 0}

    flac = svc.archive.path(MID, "flac")
    flac.write_bytes(flac.read_bytes()[:-10] + b"bitrot!!!!")
    svc.archive.path(MID2, "mp3").unlink()
    stray = svc.archive.root / "messages/2026/11/2026-11-07_101010_zzzz.flac"
    stray.write_bytes(flac_bytes())
    r = integrity_check(svc.archive)
    assert not r["ok"]
    assert r["changed"] == [f"messages/2026/11/{MID}.flac"]
    assert r["missing"] == [f"messages/2025/12/{MID2}.mp3"]
    assert r["untracked"] == ["messages/2026/11/2026-11-07_101010_zzzz.flac"]


def test_integrity_job_runs_weekly_and_on_demand(logged_in, svc, agent_headers):
    seed(logged_in, agent_headers, svc)
    svc.worker.housekeeping(force=True)       # never checked → due now
    svc.worker.run_all()
    first = logged_in.get("/api/backup").json()["integrity"]
    assert first["ok"] and first["files"] == 4
    svc.worker.housekeeping(force=True)       # checked today → not due
    with svc.db.connect() as c:
        assert not c.execute("SELECT 1 FROM jobs WHERE kind='verify' AND status='pending'").fetchone()
    assert logged_in.post("/api/backup/verify").status_code == 200
    assert logged_in.get("/api/backup").json()["verifying"] is True
    svc.worker.run_all()
    assert logged_in.get("/api/backup").json()["verifying"] is False


def test_backup_page_stats(logged_in, svc, agent_headers):
    seed(logged_in, agent_headers, svc)
    b = logged_in.get("/api/backup").json()
    kinds = b["archive"]["by_kind"]
    assert kinds["recordings"] > 0 and kinds["playback copies"] > 0 and kinds["transcripts and details"] > 0
    assert b["archive"]["bytes"] == sum(kinds.values())
    assert b["messages"]["total"] == 2 and b["viewer_updated"]


def test_backup_script_report(settings, agent_headers):
    from fastapi.testclient import TestClient

    from phonehome_server.app import create_app

    settings.backup_token = "bk"
    app = create_app(settings, transcriber=None)
    with TestClient(app) as c:  # no CSRF header: it's a script with a token
        assert c.post("/api/backup/report", json={}).status_code == 401
        assert c.post("/api/backup/report", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
        r = c.post("/api/backup/report", headers={"Authorization": "Bearer bk"},
                   json={"destination": "E:\\PhoneHome", "copied": 12, "files": 40, "verified": 40, "problems": [],
                         "computer": "DESKTOP", "evil": "ignored"})
        assert r.status_code == 200
        app.state.svc.auth.set_user("jacob", "correct horse")
        c.post("/api/auth/login", json={"username": "jacob", "password": "correct horse"}, headers=CSRF)
        b = c.get("/api/backup").json()
        assert b["last_backup"]["copied"] == 12 and "evil" not in b["last_backup"]
        assert b["last_any"] == b["last_backup"]["at"]
        hp = c.get("/api/homepage").json()
        assert hp["backup"] == "just now" and hp["integrity"] == "not checked yet"


def test_homepage_reports_integrity_problems(client, svc, agent_headers):
    from phonehome_server.backup import kv_set

    kv_set(svc.db, "integrity", {"checked_at": "2026-11-01T00:00:00+00:00", "ok": False})
    assert client.get("/api/homepage?token=hp").json()["integrity"] == "PROBLEM"
    assert client.get("/api/homepage?token=hp").json()["backup"] == "never"
