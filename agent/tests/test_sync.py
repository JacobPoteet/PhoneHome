import hashlib

import pytest

from conftest import SR, make_rig, wait_until
from fake_server import FakeServer, flac_bytes
from phonehome_agent.audio.assets import AssetLibrary
from phonehome_agent.sync import ReplaySource, ServerClient, SyncService


def make_sync(rig, server):
    client = ServerClient("http://phonehome.test", server.token, "test-phone", transport=server.transport())
    assets = AssetLibrary(rig.store.assets, SR, rig.behavior.asset_filename)
    return client, SyncService(client, rig.store, rig.behavior, assets, lambda: {"ok": True},
                               heartbeat_interval_s=0.05, config_sync_interval_s=0.05)


async def leave_message(rig, seconds=1.5):
    from phonehome_agent.controller import State

    await rig.lift()
    await rig.controller.wait_for_state(State.RECORDING)
    await wait_until(lambda: rig.controller.active and rig.controller.active.seconds >= seconds)
    await rig.hang_up()
    await rig.settle()


async def test_upload_moves_message_to_sent_after_checksum_match(rig):
    server = FakeServer()
    client, sync = make_sync(rig, server)
    await leave_message(rig)
    msg_id = rig.store.outbox_ids()[0]
    assert await sync.upload_pending() == 1
    assert rig.store.outbox_ids() == []
    assert (rig.store.sent / f"{msg_id}.flac").exists()
    stored = server.messages[msg_id]
    assert stored["meta"]["kind"] == "guest"
    assert hashlib.sha256(stored["audio"]).hexdigest() == stored["meta"]["audio"]["sha256"]
    await client.aclose()


async def test_checksum_mismatch_keeps_message_in_outbox(rig):
    server = FakeServer()
    server.corrupt_uploads = True
    client, sync = make_sync(rig, server)
    await leave_message(rig)
    with pytest.raises(RuntimeError, match="checksum"):
        await sync.upload_pending()
    assert len(rig.store.outbox_ids()) == 1
    await client.aclose()


async def test_server_down_then_back_flushes_outbox(rig):
    server = FakeServer()
    server.down = True
    client, sync = make_sync(rig, server)
    await leave_message(rig)
    with pytest.raises(Exception):
        await sync.upload_pending()
    assert len(rig.store.outbox_ids()) == 1
    server.down = False
    assert await sync.upload_pending() == 1
    await client.aclose()


async def test_config_sync_downloads_assets_and_uses_etag(rig):
    server = FakeServer()
    greeting = flac_bytes(0.5)
    server.assets["a-xmas"] = greeting
    server.config = {
        "version": "srv-2",
        "greetings": [{"id": "g-xmas", "asset": "a-xmas"}],
        "assets": {"a-xmas": {"filename": "a-xmas.flac", "sha256": hashlib.sha256(greeting).hexdigest()}},
    }
    client, sync = make_sync(rig, server)
    assert await sync.sync_config() is True
    assert rig.behavior.current.version == "srv-2"
    assert (rig.store.assets / "a-xmas.flac").read_bytes() == greeting
    assert await sync.sync_config() is False  # 304 Not Modified
    await client.aclose()


async def test_bad_asset_checksum_leaves_old_config_in_place(rig):
    server = FakeServer()
    server.assets["a1"] = b"not what was promised"
    server.config = {"version": "srv-3", "assets": {"a1": {"filename": "a1.flac", "sha256": "f" * 64}}}
    client, sync = make_sync(rig, server)
    with pytest.raises(RuntimeError, match="checksum"):
        await sync.sync_config()
    assert rig.behavior.current.version == "test-1"
    await client.aclose()


async def test_background_loops_heartbeat_and_follow_config_version(rig):
    import asyncio

    server = FakeServer()
    server.config = {"version": "srv-9"}
    client, sync = make_sync(rig, server)
    seen = []
    sync.on_indicator = seen.append
    tasks = [asyncio.create_task(t) for t in sync.tasks()]
    try:
        await wait_until(lambda: server.heartbeats and rig.behavior.current.version == "srv-9", timeout=3)
        assert sync.server_reachable is True
        assert seen and seen[-1] == "on"
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()


async def test_replay_comes_from_server_when_available(tmp_path):
    rig = make_rig(tmp_path)
    server = FakeServer()
    server.replay = ("2026-01-01_100000_abcd", flac_bytes(1.0))
    client = ServerClient("http://phonehome.test", server.token, "t", transport=server.transport())
    replay = await ReplaySource(client, rig.store, SR)()
    assert replay.message_id == "2026-01-01_100000_abcd"
    assert abs(len(replay.samples) - SR) <= 1
    server.replay = None
    assert await ReplaySource(client, rig.store, SR)() is None
    await client.aclose()
