import numpy as np
import soundfile as sf

from conftest import SR
from phonehome_agent.audio.sources import VoiceLikeSource
from phonehome_agent.behavior import RecordingSettings
from phonehome_agent.recording import Recorder
from phonehome_agent.store import LocalStore, read_json, write_json_atomic


class _FakeEngine:
    sample_rate = SR

    def add_input_listener(self, fn): ...
    def remove_input_listener(self, fn): ...


def _write_incoming(store, rec_id, samples):
    write_json_atomic(store.incoming / f"{rec_id}.json",
                      {"schema_version": 1, "id": rec_id, "kind": "guest", "sample_rate": SR})
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    (store.incoming / f"{rec_id}.pcm").write_bytes(pcm.tobytes())


def test_recover_interrupted_recording_after_power_loss(tmp_path):
    store = LocalStore(tmp_path)
    rec = Recorder(_FakeEngine(), store, device_name="t")
    voice = VoiceLikeSource(SR)(SR * 3)
    _write_incoming(store, "2026-11-06_120000_abcd", voice)
    # A truncated trailing byte (power cut mid-write) must not break recovery.
    with open(store.incoming / "2026-11-06_120000_abcd.pcm", "ab") as fh:
        fh.write(b"\x01")

    results = rec.recover(RecordingSettings())
    assert len(results) == 1 and results[0].saved
    meta = read_json(store.outbox / "2026-11-06_120000_abcd.json")
    assert meta["recovered"] is True and meta["ended_by"] == "recovered"
    data, sr = sf.read(str(store.outbox / "2026-11-06_120000_abcd.flac"))
    assert sr == SR and abs(len(data) - SR * 3) <= 1
    assert not list(store.incoming.iterdir())


def test_orphan_metadata_without_audio_is_cleaned_up(tmp_path):
    store = LocalStore(tmp_path)
    rec = Recorder(_FakeEngine(), store, device_name="t")
    write_json_atomic(store.incoming / "2026-11-06_120000_wxyz.json", {"id": "2026-11-06_120000_wxyz"})
    results = rec.recover(RecordingSettings())
    assert results[0].saved is False and results[0].reason == "too_short"
    assert not list(store.incoming.iterdir())


def test_store_retention_never_touches_outbox(tmp_path):
    import os
    import time

    store = LocalStore(tmp_path)
    for folder in (store.outbox, store.sent, store.discarded):
        for ext in (".flac", ".json"):
            p = folder / f"2020-01-01_000000_aaaa{ext}"
            p.write_text("x")
            old = time.time() - 90 * 86400
            os.utime(p, (old, old))
    store.prune(sent_days=30, discarded_days=7)
    assert store.outbox_ids() == ["2020-01-01_000000_aaaa"]
    assert not list(store.sent.iterdir()) and not list(store.discarded.iterdir())
    store.make_room(10**18)  # impossible target: deletes everything *allowed*
    assert store.outbox_ids() == ["2020-01-01_000000_aaaa"]
