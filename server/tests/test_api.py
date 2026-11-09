import json

from conftest import CSRF, upload

MID = "2026-11-06_193210_k3f9"


def test_api_requires_login(client):
    assert client.get("/api/messages").status_code == 401
    assert client.get("/api/search?q=x").status_code == 401
    assert client.get(f"/media/{MID}.flac").status_code == 401
    assert client.get("/api/auth/me").json() == {"user": None, "first_run": True, "version": "0.1.0"}


def test_first_run_setup_then_closed(client, svc):
    r = client.post("/api/auth/setup", json={"username": "jacob", "password": "long enough"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["user"] == "jacob"
    assert client.post("/api/auth/setup", json={"username": "evil", "password": "long enough"}).status_code == 403
    assert svc.auth.users() == ["jacob"]


def test_setup_rejects_short_password(client):
    r = client.post("/api/auth/setup", json={"username": "jacob", "password": "short"})
    assert r.status_code == 400 and "8 characters" in r.json()["detail"]


def test_login_logout(client, svc):
    svc.auth.set_user("jacob", "correct horse")
    assert client.post("/api/auth/login", json={"username": "jacob", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "jacob", "password": "correct horse"}).status_code == 200
    assert client.get("/api/messages").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/messages").status_code == 401


def test_state_changes_need_the_csrf_header(logged_in, agent_headers):
    upload(logged_in, agent_headers)  # the phone's API is exempt (token auth)
    r = logged_in.patch(f"/api/messages/{MID}", json={"title": "x"}, headers={"X-Requested-With": ""})
    assert r.status_code == 403
    assert logged_in.patch(f"/api/messages/{MID}", json={"title": "x"}).status_code == 200


def test_list_paging_and_filters(logged_in, svc, agent_headers):
    from conftest import flac_bytes

    ids = [f"2026-11-0{d}_120000_ab{d}c" for d in range(1, 6)]
    for i, mid in enumerate(ids):
        upload(logged_in, agent_headers, msg_id=mid, audio=flac_bytes(freq=300 + i * 50))
    page = logged_in.get("/api/messages?limit=2").json()
    assert [m["id"] for m in page["messages"]] == [ids[4], ids[3]]
    page2 = logged_in.get(f"/api/messages?limit=2&before={page['next_before']}").json()
    assert [m["id"] for m in page2["messages"]] == [ids[2], ids[1]]
    svc.messages.update(ids[0], {"favorite": True})
    assert [m["id"] for m in logged_in.get("/api/messages?show=favorites").json()["messages"]] == [ids[0]]
    logged_in.post(f"/api/messages/{ids[1]}/heard", json={})
    unheard = logged_in.get("/api/messages?show=unheard").json()["messages"]
    assert ids[1] not in [m["id"] for m in unheard] and len(unheard) == 4
    logged_in.post("/api/messages/heard-all")
    assert logged_in.get("/api/stats").json()["unheard"] == 0


def test_message_detail_and_edit(logged_in, svc, agent_headers):
    upload(logged_in, agent_headers)
    m = logged_in.get(f"/api/messages/{MID}").json()
    assert m["greeting_title"] == "Chime (default)"  # builtin:chime alias
    assert m["details"]["device"] == "test-phone"
    assert m["audio_url"] == f"/media/{MID}.flac"
    m = logged_in.patch(f"/api/messages/{MID}", json={
        "title": " Grandma's visit ", "people": ["Grandma", "Grandpa", "Grandma"], "tags": "thanksgiving, family",
        "favorite": True, "id": "hacked", "kind": "hacked"}).json()
    assert m["title"] == "Grandma's visit" and m["people"] == ["Grandma", "Grandpa"]
    assert m["tags"] == ["thanksgiving", "family"] and m["id"] == MID and m["kind"] == "guest"
    side = svc.archive.read_sidecar(MID)
    assert side["title"] == "Grandma's visit" and side["favorite"] is True and side["kind"] == "guest"
    assert logged_in.get("/api/messages/2026-01-01_000000_zzzz").status_code == 404


def test_trash_restore_purge(logged_in, svc, agent_headers):
    upload(logged_in, agent_headers)
    svc.worker.run_all()  # mp3 + transcript files must travel with it
    assert logged_in.delete(f"/api/messages/{MID}").status_code == 200
    for name in (f"{MID}.flac", f"{MID}.mp3", f"{MID}.json", f"{MID}.txt", f"{MID}.vtt", f"{MID}.transcript.json"):
        assert (svc.archive.trash / "messages/2026/11" / name).exists(), name
    assert json.loads((svc.archive.trash / f"messages/2026/11/{MID}.json").read_text())["id"] == MID
    sums = svc.archive.read_sums()
    assert f".trash/messages/2026/11/{MID}.flac" in sums and f"messages/2026/11/{MID}.flac" not in sums
    assert logged_in.get("/api/messages").json()["messages"] == []
    assert logged_in.get("/api/messages?trashed=true").json()["messages"][0]["id"] == MID
    assert logged_in.get(f"/media/{MID}.flac").status_code == 200
    assert logged_in.delete(f"/api/messages/{MID}").status_code == 409

    assert logged_in.post(f"/api/messages/{MID}/restore").status_code == 200
    assert svc.archive.path(MID, "flac").exists()
    assert f"messages/2026/11/{MID}.flac" in svc.archive.read_sums()

    svc.messages.trash(MID)
    assert svc.messages.purge_trash(older_than_days=-1) == [MID]
    assert not svc.archive.message_files(MID, trashed=True)
    assert not any(MID in k for k in svc.archive.read_sums())


def test_media_range_and_download(logged_in, agent_headers, svc):
    upload(logged_in, agent_headers)
    r = logged_in.get(f"/media/{MID}.flac", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    assert "attachment" in logged_in.get(f"/media/{MID}.flac?download=1").headers["content-disposition"]
    assert logged_in.get(f"/media/{MID}.wav").status_code == 404
    svc.worker.run_all()
    assert "Thanksgiving" in logged_in.get(f"/media/{MID}.txt").text
    assert logged_in.get(f"/media/{MID}.vtt").text.startswith("WEBVTT")


def test_settings_behavior(logged_in, svc):
    r = logged_in.put("/api/settings/behavior", json={"settings": {"assets": {"x": {"filename": "missing.flac"}}}})
    assert r.status_code == 422 and "not found" in r.json()["detail"]
    r = logged_in.put("/api/settings/behavior", json={"settings": {"greetings": []}})
    assert r.status_code == 422 and "Greetings page" in r.json()["detail"]
    r = logged_in.put("/api/settings/behavior", json={"settings": {"recording": {"max_duration_s": 90}}})
    assert r.status_code == 200 and r.json()["version"].startswith("srv-")
    assert logged_in.get("/api/settings/behavior").json()["settings"] == {"recording": {"max_duration_s": 90}}


def test_phone_status(logged_in, agent_headers):
    assert logged_in.get("/api/phone").json()["phones"] == []
    logged_in.post("/api/agent/heartbeat", headers=agent_headers, json={"version": "0.1.0"})
    p = logged_in.get("/api/phone").json()
    assert p["phones"][0]["online"] and p["phones"][0]["status"]["version"] == "0.1.0"
    assert logged_in.get("/api/stats").json()["phone"]["online"] is True


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
    app = create_app(settings, control=AgentControl(settings, transport=httpx.MockTransport(handler)),
                     transcriber=None)
    with TestClient(app, headers=CSRF) as c:
        app.state.svc.auth.set_user("jacob", "correct horse")
        c.post("/api/auth/login", json={"username": "jacob", "password": "correct horse"})
        assert c.post("/api/phone/ring", json={"mode": "memory"}).status_code == 200
    assert calls == [("/ring", "Bearer ctl", {"mode": "memory", "force": True})]


def test_phone_action_without_control_is_an_error(logged_in):
    r = logged_in.post("/api/phone/ring", json={})
    assert r.status_code == 502 and "agent_control_url" in r.json()["detail"]


def test_system_info(logged_in):
    s = logged_in.get("/api/system").json()
    assert s["transcription"]["enabled"] is True and s["transcription"]["model"] == "fake-whisper"
    assert s["users"] == ["jacob"]


def test_homepage_widget(client, agent_headers):
    assert client.get("/api/homepage").status_code == 401
    upload(client, agent_headers)
    r = client.get("/api/homepage?token=hp")
    assert r.json()["total"] == 1 and r.json()["new"] == 1 and r.json()["phone"] == "offline"


def test_web_app_shell_and_fallback(settings, tmp_path):
    from fastapi.testclient import TestClient

    from phonehome_server.app import create_app

    with TestClient(create_app(settings, transcriber=None)) as c:
        assert "hasn't been built" in c.get("/").text
    web = tmp_path / "no-web"
    (web / "_app/immutable").mkdir(parents=True)
    (web / "index.html").write_text("<html>shell</html>")
    (web / "_app/immutable/app.js").write_text("js")
    with TestClient(create_app(settings, transcriber=None)) as c:
        assert c.get("/").text == "<html>shell</html>"
        assert c.get("/m/2026-11-06_193210_k3f9").text == "<html>shell</html>"  # client-side route
        r = c.get("/_app/immutable/app.js")
        assert r.text == "js" and "immutable" in r.headers["cache-control"]
        assert c.get("/api/nope").status_code == 404  # unknown API paths don't get the shell
        assert c.get("/../../etc/passwd").text == "<html>shell</html>"
