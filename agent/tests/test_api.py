import httpx
import pytest

from phonehome_agent.app import Agent
from phonehome_agent.config import simulation_defaults


def make_agent(tmp_path, token=""):
    cfg = simulation_defaults()
    cfg.device.data_dir = str(tmp_path / "data")
    cfg.audio.backend = "virtual"
    cfg.audio.sample_rate = 16000
    cfg.control_api.token = token
    return Agent(cfg)


def client_for(agent):
    from phonehome_agent.api import create_app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(agent)), base_url="http://agent")


async def test_health_is_public_but_everything_else_needs_the_token(tmp_path):
    agent = make_agent(tmp_path, token="t0ken")
    async with client_for(agent) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/status")).status_code == 401
        r = await c.get("/status", headers={"Authorization": "Bearer t0ken"})
        assert r.status_code == 200
        body = r.json()
        assert body["controller"]["state"] == "idle"
        assert body["simulate"] is True


async def test_ring_endpoint_queues_once_and_rejects_bad_mode(tmp_path):
    agent = make_agent(tmp_path)
    async with client_for(agent) as c:
        assert (await c.post("/ring", json={"mode": "prompt"})).status_code == 200
        r = await c.post("/ring", json={"mode": "prompt"})
        assert r.status_code == 409 and "pending" in r.json()["detail"]
        assert (await c.post("/ring", json={"mode": "dance"})).status_code == 409


async def test_sim_hook_and_logs(tmp_path):
    agent = make_agent(tmp_path)
    await agent.hook.start(agent.controller.set_hook)
    async with client_for(agent) as c:
        assert (await c.post("/sim/hook", json={"off_hook": True})).status_code == 200
        assert agent.controller.off_hook is True
        assert "lines" in (await c.get("/logs?lines=5")).json()
        assert (await c.get("/sim")).status_code == 200


async def test_reboot_refused_in_simulation(tmp_path):
    agent = make_agent(tmp_path)
    async with client_for(agent) as c:
        assert (await c.post("/reboot")).status_code == 403


def test_real_config_requires_a_control_token():
    from phonehome_agent.config import AgentConfig, validate

    with pytest.raises(ValueError, match="token"):
        validate(AgentConfig())
