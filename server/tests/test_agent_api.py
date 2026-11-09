import hashlib
import json

from conftest import flac_bytes, make_meta, upload


def test_upload_requires_token(client):
    r = upload(client, {})
    assert r.status_code == 401
    r = upload(client, {"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_upload_stores_flac_sidecar_checksum_and_index(client, svc, agent_headers):
    audio = flac_bytes()
    r = upload(client, agent_headers, audio=audio)
    assert r.status_code == 201
    digest = hashlib.sha256(audio).hexdigest()
    assert r.json() == {"id": "2026-11-06_193210_k3f9", "sha256": digest, "created": True}

    root = svc.archive.root
    flac = root / "messages/2026/11/2026-11-06_193210_k3f9.flac"
    assert flac.read_bytes() == audio
    sidecar = json.loads(flac.with_suffix(".json").read_text())
    assert sidecar["kind"] == "guest"
    assert sidecar["title"] == "" and sidecar["tags"] == [] and sidecar["replay_eligible"] is True
    assert sidecar["audio"]["sha256"] == digest
    assert sidecar["received_at"]
    assert f"{digest}  messages/2026/11/2026-11-06_193210_k3f9.flac" in (root / "SHA256SUMS").read_text()
    assert (root / "README.txt").exists()

    msg = svc.messages.get("2026-11-06_193210_k3f9")
    assert msg["heard"] is False and msg["duration_s"] == 2.0
    assert not list((root / ".tmp").iterdir())  # temp upload cleaned up


def test_duplicate_upload_is_idempotent(client, agent_headers):
    audio = flac_bytes()
    assert upload(client, agent_headers, audio=audio).status_code == 201
    r = upload(client, agent_headers, audio=audio)
    assert r.status_code == 200
    assert r.json()["created"] is False
    assert r.json()["sha256"] == hashlib.sha256(audio).hexdigest()


def test_conflicting_upload_with_same_id_is_refused(client, svc, agent_headers):
    assert upload(client, agent_headers, audio=flac_bytes(freq=440)).status_code == 201
    r = upload(client, agent_headers, audio=flac_bytes(freq=660))
    assert r.status_code == 409


def test_corrupted_upload_is_refused_and_not_stored(client, svc, agent_headers):
    audio = flac_bytes()
    meta = make_meta(audio=audio)
    meta["audio"]["sha256"] = "0" * 64
    r = upload(client, agent_headers, audio=audio, meta=meta)
    assert r.status_code == 422
    assert not svc.archive.path("2026-11-06_193210_k3f9", "flac").exists()


def test_rejects_bad_ids_and_non_flac(client, agent_headers):
    r = upload(client, agent_headers, meta=make_meta("../../etc/passwd"))
    assert r.status_code == 422
    r = upload(client, agent_headers, audio=b"not audio", meta=make_meta())
    assert r.status_code == 422


def test_mp3_job_makes_playback_copy(client, svc, agent_headers):
    upload(client, agent_headers)
    assert svc.worker.run_all() == 1
    msg = svc.messages.get("2026-11-06_193210_k3f9")
    assert msg["has_mp3"]
    mp3 = svc.archive.path(msg["id"], "mp3")
    assert mp3.stat().st_size > 0
    assert "messages/2026/11/2026-11-06_193210_k3f9.mp3" in svc.archive.read_sums()
    assert svc.archive.read_sidecar(msg["id"])["playback"]["format"] == "mp3"


def test_heartbeat_records_status_and_reports_unheard(client, svc, agent_headers):
    r = client.post("/api/agent/heartbeat", headers=agent_headers, json={"version": "0.1.0", "store": {"outbox": 0}})
    assert r.status_code == 200
    assert r.json()["indicator"] == "off"
    upload(client, agent_headers)
    r = client.post("/api/agent/heartbeat", headers=agent_headers, json={})
    assert r.json()["indicator"] == "on"
    phone = svc.phones.primary()
    assert phone["device"] == "test-phone" and phone["online"]


def test_config_etag_and_version_match_heartbeat(client, svc, agent_headers):
    r = client.get("/api/agent/config", headers=agent_headers)
    assert r.status_code == 200
    etag = r.headers["ETag"]
    assert r.json()["version"] == etag.strip('"')
    assert client.get("/api/agent/config", headers={**agent_headers, "If-None-Match": etag}).status_code == 304
    hb = client.post("/api/agent/heartbeat", headers=agent_headers, json={}).json()
    assert hb["config_version"] == r.json()["version"]

    svc.behavior.save({"recording": {"max_duration_s": 60}})
    r2 = client.get("/api/agent/config", headers={**agent_headers, "If-None-Match": etag})
    assert r2.status_code == 200
    assert r2.json()["recording"]["max_duration_s"] == 60
    assert r2.json()["version"] != r.json()["version"]


def test_assets_are_served_by_id_with_checksums(client, svc, agent_headers):
    data = flac_bytes(1.0)
    (svc.archive.root / "greetings" / "xmas.flac").write_bytes(data)
    svc.behavior.save({"assets": {"xmas-2026": {"filename": "xmas.flac"}}})
    doc = client.get("/api/agent/config", headers=agent_headers).json()
    assert doc["assets"]["xmas-2026"]["sha256"] == hashlib.sha256(data).hexdigest()
    r = client.get("/api/agent/assets/xmas-2026", headers=agent_headers)
    assert r.status_code == 200 and r.content == data
    assert client.get("/api/agent/assets/nope", headers=agent_headers).status_code == 404


def test_replay_random(client, svc, agent_headers):
    assert client.get("/api/agent/replay/random", headers=agent_headers).status_code == 204
    audio = flac_bytes()
    upload(client, agent_headers, audio=audio)
    r = client.get("/api/agent/replay/random", headers=agent_headers)
    assert r.status_code == 200
    assert r.content == audio
    assert r.headers["X-Message-Id"] == "2026-11-06_193210_k3f9"
    assert r.headers["X-Recorded-At"].startswith("2026-11-06T19:32:10")
    svc.messages.update("2026-11-06_193210_k3f9", {"replay_eligible": False})
    assert client.get("/api/agent/replay/random", headers=agent_headers).status_code == 204
