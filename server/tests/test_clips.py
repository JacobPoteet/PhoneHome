"""Greetings and prompts manager."""

import io
from datetime import datetime

import numpy as np
import soundfile as sf

from conftest import upload

MID = "2026-11-06_193210_k3f9"


def wav_bytes(seconds=1.5, sr=44100, freq=440):
    t = np.arange(int(seconds * sr)) / sr
    data = np.concatenate([np.zeros(int(0.5 * sr)), 0.2 * np.sin(2 * np.pi * freq * t), np.zeros(int(0.5 * sr))])
    buf = io.BytesIO()
    sf.write(buf, data.astype("float32"), sr, format="WAV")
    return buf.getvalue()


def add_clip(c, kind="greeting", title="Christmas", text="", data=None, name="xmas.wav"):
    return c.post("/api/clips", data={"kind": kind, "title": title, "text": text},
                  files={"audio": (name, data or wav_bytes(), "audio/wav")})


def test_builtin_chime_is_seeded(logged_in):
    r = logged_in.get("/api/clips").json()
    [chime] = r["greetings"]
    assert chime["id"] == "builtin-chime" and chime["builtin"] and chime["active_chance"] == 1.0
    assert r["prompts"] == []
    assert logged_in.delete("/api/clips/builtin-chime").status_code == 422


def test_upload_normalizes_to_48k_mono_flac_and_keeps_source(logged_in, svc):
    r = add_clip(logged_in)
    assert r.status_code == 201
    clip = r.json()
    assert clip["id"].startswith("christmas-") and clip["enabled"] is False  # off until you're ready
    flac = svc.archive.root / "greetings" / f"{clip['id']}.flac"
    info = sf.info(str(flac))
    assert info.samplerate == 48000 and info.channels == 1
    assert 1.3 < clip["duration_s"] < 1.7  # the silence at both ends was trimmed
    assert (svc.archive.root / "greetings" / f"{clip['id']}.source.wav").exists()
    assert logged_in.get(clip["audio_url"]).status_code == 200


def test_rejects_non_audio(logged_in):
    r = add_clip(logged_in, data=b"this is not audio", name="notes.txt")
    assert r.status_code == 422


def test_rules_priority_and_what_plays_now(logged_in):
    clip = add_clip(logged_in).json()
    r = logged_in.patch(f"/api/clips/{clip['id']}", json={
        "enabled": True, "priority": 10, "rules": {"dates": [{"start": "12-01", "end": "12-31"}]}})
    assert r.status_code == 200 and r.json()["rules_summary"] == "Dec 1 – Dec 31"
    dec = logged_in.get("/api/clips?at=2026-12-15T10:00:00-05:00").json()["greetings"]
    by_id = {c["id"]: c["active_chance"] for c in dec}
    assert by_id == {clip["id"]: 1.0, "builtin-chime": None}  # higher priority wins
    june = {c["id"]: c["active_chance"] for c in logged_in.get("/api/clips?at=2026-06-15T10:00").json()["greetings"]}
    assert june == {clip["id"]: None, "builtin-chime": 1.0}

    # Same priority = weighted rotation.
    logged_in.patch(f"/api/clips/{clip['id']}", json={"priority": 0, "weight": 3, "rules": {}})
    now = {c["id"]: c["active_chance"] for c in logged_in.get("/api/clips").json()["greetings"]}
    assert now == {clip["id"]: 0.75, "builtin-chime": 0.25}


def test_rule_validation(logged_in):
    clip = add_clip(logged_in).json()
    bad = [{"dates": [{"start": "13-01", "end": "12-31"}]}, {"dates": [{"start": "2026-12-01", "end": "12-31"}]},
           {"weekdays": [7]}, {"time_from": "17:00"}, {"time_from": "25:00", "time_to": "26:00"}]
    for rules in bad:
        assert logged_in.patch(f"/api/clips/{clip['id']}", json={"rules": rules}).status_code == 422, rules
    r = logged_in.patch(f"/api/clips/{clip['id']}", json={"rules": {
        "weekdays": [6, 5], "time_from": "17:00", "time_to": "21:00", "dates": [{"start": "02-29", "end": "03-01"}]}})
    assert r.json()["rules"] == {"dates": [{"start": "02-29", "end": "03-01"}], "weekdays": [5, 6],
                                 "time_from": "17:00", "time_to": "21:00"}
    assert r.json()["rules_summary"] == "Feb 29 – Mar 1 · weekends · 17:00–21:00"
    assert logged_in.patch(f"/api/clips/{clip['id']}", json={"weight": -1}).status_code == 422
    assert logged_in.patch(f"/api/clips/{clip['id']}", json={"title": "  "}).status_code == 422


def test_rules_match_like_the_agent():
    """The server's matcher must agree with the phone's, or 'playing now' lies."""
    import random

    import pytest

    behavior = pytest.importorskip("phonehome_agent.behavior")
    from phonehome_server.clips import rules_match

    rng = random.Random(3)
    samples = [
        {}, {"dates": [{"start": "12-20", "end": "01-05"}]}, {"dates": [{"start": "2026-07-01", "end": "2026-07-04"}]},
        {"weekdays": [5, 6]}, {"time_from": "22:00", "time_to": "06:00"}, {"time_from": "09:00", "time_to": "17:00"},
        {"dates": [{"start": "03-01", "end": "03-31"}], "weekdays": [0], "time_from": "08:00", "time_to": "12:00"},
    ]
    for _ in range(400):
        when = datetime(2026, rng.randint(1, 12), rng.randint(1, 28), rng.randint(0, 23), rng.randint(0, 59))
        for rules in samples:
            assert rules_match(rules, when) == behavior.Rules.from_dict(rules).matches(when), (rules, when)


def test_phone_receives_managed_clips(logged_in, svc, agent_headers):
    clip = add_clip(logged_in, kind="prompt", title="What's on your mind?", text="What's been on your mind lately?").json()
    doc = logged_in.get("/api/agent/config", headers=agent_headers).json()
    v1 = doc["version"]
    assert [g["id"] for g in doc["greetings"]] == ["builtin-chime"]
    assert doc["greetings"][0]["asset"] == "builtin:chime"
    [p] = doc["prompts"]
    assert p["id"] == clip["id"] and p["asset"] == clip["id"] and p["enabled"] is False
    assert doc["assets"][clip["id"]]["filename"] == f"{clip['id']}.flac"
    assert logged_in.get(f"/api/agent/assets/{clip['id']}", headers=agent_headers).status_code == 200

    logged_in.patch(f"/api/clips/{clip['id']}", json={"enabled": True})
    doc2 = logged_in.get("/api/agent/config", headers=agent_headers).json()
    assert doc2["version"] != v1 and doc2["prompts"][0]["enabled"] is True
    hb = logged_in.post("/api/agent/heartbeat", headers=agent_headers, json={}).json()
    assert hb["config_version"] == doc2["version"]

    # Settings and clips merge; a raw "assets" entry for a ring sound survives.
    (svc.archive.root / "ring-sounds" / "bell.flac").write_bytes(open(svc.archive.root / "prompts" / f"{clip['id']}.flac", "rb").read())
    logged_in.put("/api/settings/behavior", json={"settings": {"assets": {"bell": {"filename": "bell.flac"}},
                                                               "ringer": {"sound": "bell"}}})
    doc3 = logged_in.get("/api/agent/config", headers=agent_headers).json()
    assert set(doc3["assets"]) == {"bell", clip["id"]} and doc3["ringer"]["sound"] == "bell"


def test_replace_audio_and_delete(logged_in, svc):
    clip = add_clip(logged_in).json()
    old_sha = svc.clips.get(clip["id"])["sha256"]
    r = logged_in.put(f"/api/clips/{clip['id']}/audio", files={"audio": ("v2.wav", wav_bytes(freq=660), "audio/wav")})
    assert r.status_code == 200 and svc.clips.get(clip["id"])["sha256"] != old_sha
    assert logged_in.delete(f"/api/clips/{clip['id']}").status_code == 200
    assert (svc.archive.trash / "greetings" / f"{clip['id']}.flac").exists()
    assert [c["id"] for c in logged_in.get("/api/clips").json()["greetings"]] == ["builtin-chime"]
    assert logged_in.patch(f"/api/clips/{clip['id']}", json={"enabled": True}).status_code == 404


def test_messages_link_to_prompts_they_answered(logged_in, svc, agent_headers):
    from conftest import make_meta

    clip = add_clip(logged_in, kind="prompt", title="Favorite memory").json()
    meta = make_meta(MID)
    meta["kind"], meta["prompt_id"], meta["greeting_id"] = "prompted", clip["id"], None
    from conftest import flac_bytes
    audio = flac_bytes()
    import hashlib
    meta["audio"] = {"sha256": hashlib.sha256(audio).hexdigest()}
    upload(logged_in, agent_headers, audio=audio, meta=meta)
    m = logged_in.get(f"/api/messages/{MID}").json()
    assert m["prompt_title"] == "Favorite memory"
    prompts = logged_in.get("/api/clips?kind=prompt").json()["prompts"]
    assert prompts[0]["answers"] == 1
    assert logged_in.get("/api/search", params={"prompt": clip["id"]}).json()["total"] == 1
    # Deleted prompts keep their name on old answers.
    logged_in.delete(f"/api/clips/{clip['id']}")
    assert logged_in.get(f"/api/messages/{MID}").json()["prompt_title"] == "Favorite memory"
