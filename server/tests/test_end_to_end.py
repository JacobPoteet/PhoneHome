"""The real phone agent talking to the real server, in one process.

Uses the agent's virtual audio rig (agent/tests/conftest.py): pick up,
talk, hang up, then the agent's own sync code uploads to this server.
Skipped when the agent package isn't installed.
"""

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

pytest.importorskip("phonehome_agent")

from phonehome_agent.audio.assets import AssetLibrary  # noqa: E402
from phonehome_agent.controller import State  # noqa: E402
from phonehome_agent.sync import ReplaySource, ServerClient, SyncService  # noqa: E402

from conftest import TOKEN  # noqa: E402

AGENT_TESTS = Path(__file__).resolve().parents[2] / "agent" / "tests"


def _agent_conftest():
    spec = importlib.util.spec_from_file_location("agent_conftest", AGENT_TESTS / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_conftest"] = mod  # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
async def rig(tmp_path):
    import asyncio

    ac = _agent_conftest()
    r = ac.make_rig(tmp_path / "phone")
    r.wait_until = ac.wait_until
    r.SR = ac.SR
    await r.engine.start()
    r.task = asyncio.create_task(r.controller.run())
    yield r
    r.task.cancel()
    await asyncio.gather(r.task, return_exceptions=True)
    await r.controller.shutdown()
    await r.engine.stop()


def agent_client(app) -> ServerClient:
    return ServerClient("http://phonehome.test", TOKEN, "hallway-phone", transport=httpx.ASGITransport(app=app))


async def test_message_left_on_the_phone_lands_in_the_archive(rig, app, svc):
    client = agent_client(app)
    assets = AssetLibrary(rig.store.assets, rig.SR, rig.behavior.asset_filename)
    sync = SyncService(client, rig.store, rig.behavior, assets, lambda: {"device": "hallway-phone"})

    # Pick up, talk for ~1.5 s, hang up.
    await rig.lift()
    await rig.controller.wait_for_state(State.RECORDING)
    await rig.wait_until(lambda: rig.controller.active and rig.controller.active.seconds >= 1.5)
    await rig.hang_up()
    await rig.settle()
    [msg_id] = rig.store.outbox_ids()

    assert await sync.upload_pending() == 1
    assert rig.store.outbox_ids() == []  # the agent saw a matching checksum

    msg = svc.messages.get(msg_id)
    assert msg and msg["kind"] == "guest" and msg["device"] == "test-phone"
    flac = svc.archive.path(msg_id, "flac")
    assert flac.read_bytes() == (rig.store.sent / f"{msg_id}.flac").read_bytes()
    sidecar = json.loads(flac.with_suffix(".json").read_text())
    assert sidecar["levels"]["voiced_s"] > 0
    svc.worker.run_all()
    assert svc.messages.get(msg_id)["has_mp3"]

    # Re-sending (e.g. the 201 got lost) is harmless.
    meta = json.loads((rig.store.sent / f"{msg_id}.json").read_text())
    assert await client.upload_message(meta, rig.store.sent / f"{msg_id}.flac")
    await client.aclose()


async def test_config_sync_and_memory_lane_replay(rig, app, svc, agent_headers):
    from fastapi.testclient import TestClient

    from conftest import flac_bytes, upload

    with TestClient(app) as tc:
        upload(tc, agent_headers, audio=flac_bytes(1.0, sr=rig.SR))
    (svc.archive.root / "prompts" / "mind.flac").write_bytes(flac_bytes(0.5, sr=rig.SR))
    svc.behavior.save({
        "recording": {"max_duration_s": 6.0, "min_duration_s": 1.0, "pickup_settle_ms": 20,
                      "hangup_tone_timeout_s": 0.5},
        "ringer": {"ring_timeout_s": 0.4},
        "prompts": [{"id": "p-mind", "asset": "mind", "title": "What's on your mind?"}],
        "assets": {"mind": {"filename": "mind.flac"}},
    })

    client = agent_client(app)
    assets = AssetLibrary(rig.store.assets, rig.SR, rig.behavior.asset_filename)
    sync = SyncService(client, rig.store, rig.behavior, assets, lambda: {})
    assert await sync.sync_config() is True
    assert rig.behavior.current.prompts[0].id == "p-mind"
    assert (rig.store.assets / "mind.flac").exists()
    hb = await client.heartbeat({})
    assert hb["config_version"] == rig.behavior.current.version
    assert await sync.sync_config() is False  # 304, nothing changed

    replay = await ReplaySource(client, rig.store, rig.SR)()
    assert replay is not None and replay.message_id == "2026-11-06_193210_k3f9"
    assert replay.recorded_at.startswith("2026-11-06T19:32:10")
    await client.aclose()
