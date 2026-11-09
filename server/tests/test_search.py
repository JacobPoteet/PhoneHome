"""Transcription, search, and On this day."""

from datetime import datetime

from conftest import FakeTranscriber, flac_bytes, upload

MID = "2026-11-06_193210_k3f9"


def test_ingest_queues_transcription_and_writes_files(client, svc, agent_headers, transcriber):
    upload(client, agent_headers)
    assert svc.messages.get(MID)["transcript_state"] == "pending"
    svc.worker.run_all()
    assert transcriber.calls == 1
    base = svc.archive.path(MID, "flac").with_suffix("")
    assert base.with_suffix(".txt").read_text().strip() == "Happy Thanksgiving everyone. We miss you, Grandma!"
    assert "00:00:00.000 --> 00:00:02.800" in base.with_suffix(".vtt").read_text()
    assert base.with_name(MID + ".transcript.json").exists()
    m = svc.messages.get(MID)
    assert m["transcript_state"] == "done" and "Thanksgiving" in m["transcript"]
    side = svc.archive.read_sidecar(MID)
    assert side["transcript"]["model"] == "fake-whisper" and side["transcript"]["edited"] is False
    # Transcript files are not audio: not in SHA256SUMS.
    assert not any(k.endswith((".txt", ".vtt")) for k in svc.archive.read_sums())


def test_mp3_runs_before_transcription(client, svc, agent_headers):
    upload(client, agent_headers)
    with svc.db.connect() as c:
        order = [r[0] for r in c.execute("SELECT kind FROM jobs ORDER BY priority, id")]
    assert order == ["mp3", "transcribe"]


def test_human_edits_survive_retranscription(logged_in, svc, agent_headers, transcriber):
    upload(logged_in, agent_headers)
    svc.worker.run_all()
    t = logged_in.put(f"/api/messages/{MID}/transcript", json={"text": "Happy Thanksgiving, everyone! We miss you, Grandma."}).json()
    assert t["state"] == "edited" and t["edited"].endswith("Grandma.") and t["machine_text"].endswith("Grandma!")
    assert len(t["segments"]) == 1 and t["segments"][0]["words"][1]["text"] == " Thanksgiving"
    assert svc.archive.read_sidecar(MID)["transcript"]["edited"] is True

    transcriber.text = "a much better model heard this"
    assert logged_in.post(f"/api/messages/{MID}/transcribe").status_code == 200
    svc.worker.run_all()
    m = logged_in.get(f"/api/messages/{MID}").json()
    assert m["transcript"]["edited"].endswith("Grandma.")       # kept
    assert m["transcript"]["machine_text"] == "a much better model heard this"
    assert m["transcript"]["text"].endswith("Grandma.")
    assert svc.archive.read_sidecar(MID)["transcript"]["edited"] is True

    # Clearing the edit falls back to the machine text.
    t = logged_in.put(f"/api/messages/{MID}/transcript", json={"text": ""}).json()
    assert t["state"] == "done" and t["text"] == "a much better model heard this"


def test_failed_transcription_is_marked(client, svc, agent_headers):
    class Broken:
        name = "broken"

        def transcribe(self, audio):
            raise RuntimeError("model exploded")

    svc.worker.transcriber = Broken()
    upload(client, agent_headers)
    import time

    for _ in range(5):  # retries with backoff; skip the waiting
        with svc.db.connect() as c:
            c.execute("UPDATE jobs SET run_after = 0")
        svc.worker.run_all()
    time.sleep(0)
    assert svc.messages.get(MID)["transcript_state"] == "failed"
    assert svc.worker.summary()["recent_failures"][0]["last_error"] == "model exploded"


def _seed(client, agent_headers, svc):
    rows = [
        ("2024-11-28_180000_aaaa", "We're all here for Thanksgiving dinner, the turkey is huge", ["thanksgiving"], ["Grandma"]),
        ("2025-11-27_190000_bbbb", "Happy thanksgivings from the cousins", ["thanksgiving", "cousins"], ["Sam"]),
        ("2025-12-25_090000_cccc", "Merry Christmas! Santa came", ["christmas"], ["Grandma", "Sam"]),
        ("2026-03-03_120000_dddd", "Just calling to say hi, café was lovely", [], []),
    ]
    for i, (mid, text, tags, people) in enumerate(rows):
        svc.worker.transcriber = FakeTranscriber(text)
        upload(client, agent_headers, msg_id=mid, audio=flac_bytes(freq=200 + 40 * i))
        svc.worker.run_all()
        svc.messages.update(mid, {"tags": tags, "people": people})
    return [r[0] for r in rows]


def test_search_transcripts_with_stemming_prefix_and_filters(logged_in, svc, agent_headers):
    ids = _seed(logged_in, agent_headers, svc)
    s = lambda **p: logged_in.get("/api/search", params=p).json()  # noqa: E731
    r = s(q="thanksgiving")
    assert {m["id"] for m in r["results"]} == {ids[0], ids[1]} and r["total"] == 2
    assert "\x02" in r["results"][0]["snippet"]  # highlight markers around the match
    assert [m["id"] for m in s(q="turk")["results"]] == [ids[0]]            # prefix
    assert [m["id"] for m in s(q="cafe")["results"]] == [ids[3]]            # accents folded
    assert [m["id"] for m in s(q='"santa came"')["results"]] == [ids[2]]     # phrase
    assert s(q='"came santa"')["total"] == 0
    assert {m["id"] for m in s(q="grandma")["results"]} == {ids[0], ids[2]}  # people are searchable
    assert [m["id"] for m in s(q="thanksgiving", year=2025)["results"]] == [ids[1]]
    assert [m["id"] for m in s(tag="cousins")["results"]] == [ids[1]]
    assert {m["id"] for m in s(person="Sam")["results"]} == {ids[1], ids[2]}
    assert s(q='AND OR NOT ( " *')["total"] == 0  # FTS syntax is neutralized, never an error
    assert s(q='( " *')["total"] == 4             # no words at all = no text filter
    svc.messages.update(ids[2], {"favorite": True})
    assert [m["id"] for m in s(favorite="true")["results"]] == [ids[2]]
    f = s()["facets"]
    assert {"value": "thanksgiving", "count": 2} in f["tags"]
    assert f["years"][0] == {"value": "2026", "count": 1}
    assert {"value": "Grandma", "count": 2} in f["people"]

    # Edits reach the index; trashed messages drop out.
    svc.messages.update(ids[3], {"title": "Pancake breakfast"})
    assert [m["id"] for m in s(q="pancake")["results"]] == [ids[3]]
    svc.messages.trash(ids[3])
    assert s(q="pancake")["total"] == 0


def test_search_survives_reindex(logged_in, svc, agent_headers, settings):
    from phonehome_server.db import Database

    ids = _seed(logged_in, agent_headers, svc)
    svc.messages.edit_transcript(ids[3], "Calling about the pancakes")
    settings.db_path.unlink()
    for suffix in ("-wal", "-shm"):
        settings.db_path.with_name(settings.db_path.name + suffix).unlink(missing_ok=True)
    Database(settings.db_path).init()
    counts = svc.messages.reindex()
    assert counts["messages"] == 4 and counts["transcribe_queued"] == 0
    svc.auth.set_user("jacob", "correct horse")  # accounts live in the database too
    logged_in.post("/api/auth/login", json={"username": "jacob", "password": "correct horse"})
    r = logged_in.get("/api/search", params={"q": "pancakes"}).json()
    assert [m["id"] for m in r["results"]] == [ids[3]]
    assert svc.messages.get(ids[3])["transcript_state"] == "edited"


def test_on_this_day(logged_in, svc, agent_headers):
    _seed(logged_in, agent_headers, svc)
    r = logged_in.get("/api/on-this-day?date=11-28").json()
    assert r["date"] == "11-28" and [y["year"] for y in r["years"]] == [2024]
    assert logged_in.get("/api/on-this-day?date=13-40").status_code == 422
    now = datetime.now()
    assert logged_in.get("/api/on-this-day").json()["date"] == f"{now.month:02d}-{now.day:02d}"


def test_fts_query_builder():
    from phonehome_server.messages import fts_query

    assert fts_query("thanks giving") == '"thanks"* "giving"*'
    assert fts_query('"merry christmas" santa') == '"merry christmas" "santa"*'
    assert fts_query('AND ( ) * "') == '"AND"*'
    assert fts_query("") == ""
