from phonehome_server.cli import main
from phonehome_server.db import Database

from conftest import flac_bytes, upload

MID = "2026-11-06_193210_k3f9"


def test_reindex_rebuilds_database_from_files(client, svc, agent_headers, settings):
    upload(client, agent_headers)
    upload(client, agent_headers, msg_id="2026-12-25_090000_abcd", audio=flac_bytes(freq=550))
    svc.worker.run_all()
    svc.messages.update(MID, {"title": "Kept in the sidecar"})
    svc.messages.trash("2026-12-25_090000_abcd")

    settings.db_path.unlink()
    for suffix in ("-wal", "-shm"):
        settings.db_path.with_name(settings.db_path.name + suffix).unlink(missing_ok=True)
    Database(settings.db_path).init()

    counts = svc.messages.reindex()
    assert counts["messages"] == 1 and counts["trash"] == 1 and counts["errors"] == 0
    m = svc.messages.get(MID)
    assert m["title"] == "Kept in the sidecar" and m["has_mp3"] and m["deleted_at"] is None
    assert svc.messages.get("2026-12-25_090000_abcd")["deleted_at"] is not None


def test_reindex_removes_rows_for_missing_files(client, svc, agent_headers):
    upload(client, agent_headers)
    for f in svc.archive.message_files(MID):
        f.unlink()
    assert svc.messages.reindex()["removed"] == 1
    assert svc.messages.get(MID) is None


def test_verify_command(client, svc, agent_headers, settings, monkeypatch, capsys):
    upload(client, agent_headers)
    monkeypatch.setenv("PHONEHOME_ARCHIVE_DIR", str(settings.archive_dir))
    monkeypatch.setenv("PHONEHOME_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("PHONEHOME_CONFIG", "/nonexistent.toml")
    assert main(["verify"]) == 0
    flac = svc.archive.path(MID, "flac")
    flac.write_bytes(flac.read_bytes() + b"x")
    assert main(["verify"]) == 1
    assert "CHANGED" in capsys.readouterr().out


def test_settings_from_env_and_toml(tmp_path):
    from phonehome_server.config import load_settings

    cfg = tmp_path / "server.toml"
    cfg.write_text('agent_token = "from-file"\nport = 9000\narchive_dir = "/nas/phonehome"\n')
    s = load_settings(cfg, env={"PHONEHOME_PORT": "9100", "PHONEHOME_COOKIE_SECURE": "true"})
    assert s.agent_token == "from-file"
    assert s.port == 9100 and s.cookie_secure is True
    assert str(s.archive_dir) == "/nas/phonehome"


def test_passwords_are_hashed(svc):
    svc.auth.set_user("jacob", "correct horse")
    with svc.db.connect() as c:
        stored = c.execute("SELECT pw_hash FROM users").fetchone()[0]
    assert "correct horse" not in stored and stored.startswith("scrypt$")
    assert svc.auth.login("jacob", "correct horse")
    assert svc.auth.login("jacob", "nope") is None
    assert svc.auth.login("nobody", "correct horse") is None
