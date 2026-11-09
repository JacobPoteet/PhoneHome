import json

from conftest import upload

MID = "2026-11-06_193210_k3f9"


def test_pages_require_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/api/messages").status_code == 401
    assert client.get(f"/media/{MID}.flac", follow_redirects=False).status_code == 303


def test_first_run_creates_account_then_setup_closes(client, svc):
    r = client.get("/login")
    assert "Create account" in r.text
    r = client.post("/setup", data={"username": "jacob", "password": "long enough"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/").status_code == 200
    r = client.post("/setup", data={"username": "evil", "password": "long enough"})
    assert r.status_code == 403
    assert svc.auth.users() == ["jacob"]


def test_wrong_password(client, svc):
    svc.auth.set_user("jacob", "correct horse")
    r = client.post("/login", data={"username": "jacob", "password": "wrong"})
    assert r.status_code == 401
    assert "Wrong username or password" in r.text


def test_login_next_cannot_redirect_offsite(client, svc):
    svc.auth.set_user("jacob", "correct horse")
    r = client.post("/login", data={"username": "jacob", "password": "correct horse", "next": "//evil.com"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"


def test_timeline_and_message_page(logged_in, agent_headers):
    r = logged_in.get("/")
    assert "No messages yet" in r.text
    upload(logged_in, agent_headers)
    r = logged_in.get("/")
    assert f"/m/{MID}" in r.text
    assert "Friday, November 6, 2026" in r.text
    r = logged_in.get(f"/m/{MID}")
    assert r.status_code == 200
    assert "7:32 pm" in r.text
    assert logged_in.get("/m/2026-01-01_000000_zzzz").status_code == 404


def test_edit_updates_sidecar_and_index(logged_in, svc, agent_headers):
    upload(logged_in, agent_headers)
    r = logged_in.post(f"/m/{MID}/edit", data={"title": " Grandma's visit ", "people": "Grandma, Grandpa, Grandma",
                                               "tags": "thanksgiving", "notes": "First one!", "favorite": "on"},
                       follow_redirects=False)
    assert r.status_code == 303
    side = svc.archive.read_sidecar(MID)
    assert side["title"] == "Grandma's visit"
    assert side["people"] == ["Grandma", "Grandpa"]
    assert side["favorite"] is True
    assert side["replay_eligible"] is False  # unchecked box
    assert side["kind"] == "guest"  # untouched
    msg = svc.messages.get(MID)
    assert msg["title"] == "Grandma's visit" and msg["tags"] == ["thanksgiving"]


def test_trash_restore_and_purge(logged_in, svc, agent_headers):
    upload(logged_in, agent_headers)
    svc.worker.run_all()
    logged_in.post(f"/m/{MID}/trash")
    assert not svc.archive.path(MID, "flac").exists()
    assert svc.archive.path(MID, "flac", trashed=True).exists()
    assert svc.archive.path(MID, "mp3", trashed=True).exists()
    sums = svc.archive.read_sums()
    assert f".trash/messages/2026/11/{MID}.flac" in sums and f"messages/2026/11/{MID}.flac" not in sums
    assert MID not in logged_in.get("/").text
    assert MID in logged_in.get("/trash").text
    # Trashed audio is still playable from its page.
    assert logged_in.get(f"/media/{MID}.flac").status_code == 200

    logged_in.post(f"/m/{MID}/restore")
    assert svc.archive.path(MID, "flac").exists()
    assert f"messages/2026/11/{MID}.flac" in svc.archive.read_sums()

    svc.messages.trash(MID)
    assert svc.messages.purge_trash(older_than_days=30) == []
    assert svc.messages.purge_trash(older_than_days=-1) == [MID]
    assert not svc.archive.message_files(MID, trashed=True)
    assert svc.messages.get(MID) is None
    assert not any(MID in k for k in svc.archive.read_sums())


def test_media_supports_range_requests(logged_in, svc, agent_headers):
    upload(logged_in, agent_headers)
    r = logged_in.get(f"/media/{MID}.flac", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    r = logged_in.get(f"/media/{MID}.flac?download=1")
    assert "attachment" in r.headers["content-disposition"]
    assert logged_in.get(f"/media/{MID}.wav").status_code == 404


def test_heard_and_json_api(logged_in, svc, agent_headers):
    upload(logged_in, agent_headers)
    assert logged_in.get("/api/messages").json()["messages"][0]["heard"] is False
    assert logged_in.post(f"/api/messages/{MID}/heard").status_code == 200
    m = logged_in.get(f"/api/messages/{MID}").json()
    assert m["heard"] is True and m["audio_url"].endswith(".flac")
    m = logged_in.patch(f"/api/messages/{MID}", json={"title": "Hi", "id": "hacked"}).json()
    assert m["title"] == "Hi" and m["id"] == MID
    assert logged_in.delete(f"/api/messages/{MID}").status_code == 200
    assert logged_in.get("/api/messages?trashed=true").json()["messages"][0]["id"] == MID


def test_settings_validates_and_saves(logged_in, svc):
    r = logged_in.post("/settings", data={"behavior": "{not json"})
    assert r.status_code == 400
    r = logged_in.post("/settings", data={"behavior": json.dumps({"assets": {"x": {"filename": "missing.flac"}}})})
    assert r.status_code == 400 and "not found" in r.text
    r = logged_in.post("/settings", data={"behavior": '{"recording": {"max_duration_s": 90}}'}, follow_redirects=False)
    assert r.status_code == 303
    assert svc.behavior.load() == {"recording": {"max_duration_s": 90}}


def test_phone_page_without_and_with_heartbeat(logged_in, agent_headers):
    assert "hasn't checked in" in logged_in.get("/phone").text
    logged_in.post("/api/agent/heartbeat", headers=agent_headers, json={
        "version": "0.1.0", "uptime_s": 4000, "controller": {"state": "idle", "off_hook": False, "history": []},
        "audio": {"healthy": True}, "store": {"outbox": 0, "discarded": 2, "free_mb": 20000}, "sync": {}, "system": {}})
    r = logged_in.get("/phone")
    assert "online" in r.text and "1 h 6 min" in r.text


def test_phone_actions_call_agent_control_api(settings):
    import httpx
    from fastapi.testclient import TestClient

    from phonehome_server.app import create_app
    from phonehome_server.phone import AgentControl

    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.url.path, req.headers.get("Authorization"), json.loads(req.content or b"{}")))
        return httpx.Response(200, json={"ok": True})

    settings.agent_control_url = "http://phone.test:8081"
    settings.agent_control_token = "ctl"
    app = create_app(settings, control=AgentControl(settings, transport=httpx.MockTransport(handler)))
    with TestClient(app) as c:
        app.state.svc.auth.set_user("jacob", "correct horse")
        c.post("/login", data={"username": "jacob", "password": "correct horse"})
        r = c.post("/phone/ring", data={"mode": "memory"}, follow_redirects=False)
        assert "notice=Ringing" in r.headers["location"]
    assert calls == [("/ring", "Bearer ctl", {"mode": "memory", "force": True})]


def test_homepage_widget(client, svc, agent_headers):
    assert client.get("/api/homepage").status_code == 401
    upload(client, agent_headers)
    r = client.get("/api/homepage?token=hp")
    assert r.status_code == 200
    assert r.json()["total"] == 1 and r.json()["new"] == 1 and r.json()["phone"] == "offline"
    assert client.get("/api/homepage", headers={"Authorization": "Bearer hp"}).status_code == 200
