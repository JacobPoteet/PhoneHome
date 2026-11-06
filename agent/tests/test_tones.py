import numpy as np
import pytest

from phonehome_agent.audio import tones


@pytest.mark.parametrize("name", sorted(tones.BUILTIN))
def test_builtin_sounds_are_sane(name):
    x = tones.BUILTIN[name](48000)
    assert x.dtype == np.float32
    assert len(x) > 0
    assert np.all(np.isfinite(x))
    assert np.max(np.abs(x)) <= 0.6  # headroom: nothing plays at full scale


def test_ring_cadence_is_two_on_four_off():
    sr = 8000
    x = tones.ring_electronic(sr)
    assert len(x) == 6 * sr
    assert np.max(np.abs(x[: 2 * sr])) > 0.1
    assert np.max(np.abs(x[2 * sr + 10:])) == 0.0


async def test_engine_sanitizes_nan_input():
    from phonehome_agent.audio.engine import VirtualAudioEngine

    seen = []
    e = VirtualAudioEngine(input_source=lambda n: np.full(n, np.nan, dtype=np.float32), speed=0,
                           sample_rate=8000, blocksize=64, output_channels=2,
                           routes={"earpiece": [0], "ringer": [1]})
    e.add_input_listener(lambda b: seen.append(b.copy()))
    e._process(e.input_source(64), 64)
    assert np.all(seen[0] == 0.0)
    assert e.bad_input_blocks == 1
    assert np.isfinite(e.input_level_dbfs)
