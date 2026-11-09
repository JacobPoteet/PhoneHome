"""The offline viewer: one self-contained index.html for the whole archive.

It lists every message with a player, date, people, tags, notes and the
transcript, and plays the audio straight from the files next to it. No
server, no internet, no app: double-click it on a USB stick in 2045 and
it works in any browser. It's plain HTML (readable even with JavaScript
off); a few lines of script add the search box.

It's built from the files (sidecars and transcripts), not the database,
like everything that has to outlive the software.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from pathlib import Path

from . import __version__
from .archive import Archive, write_bytes_atomic

log = logging.getLogger(__name__)

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _when(recorded_at: str) -> tuple[str, str]:
    """("Friday, November 6, 2026", "7:32 pm") in the phone's local time as recorded."""
    try:
        d = datetime.fromisoformat(recorded_at)
    except (TypeError, ValueError):
        return recorded_at or "", ""
    date = f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}, {d.year}"
    time = f"{d.hour % 12 or 12}:{d.minute:02d} {'am' if d.hour < 12 else 'pm'}"
    return date, time


def _duration(seconds) -> str:
    s = int(round(float(seconds or 0)))
    return f"{s // 60}:{s % 60:02d}"


def collect(archive: Archive) -> list[dict]:
    """Every message in the archive (not the trash), oldest first."""
    out = []
    for msg_id, path in archive.iter_sidecars():
        try:
            meta = json.loads(path.read_text())
        except ValueError:
            log.warning("viewer: unreadable %s", path)
            continue
        base = path.with_suffix("")
        edited, machine = base.with_name(base.name + ".edited.txt"), base.with_name(base.name + ".txt")
        transcript = (edited if edited.exists() else machine if machine.exists() else None)
        mp3 = base.with_name(base.name + ".mp3")
        audio = mp3 if mp3.exists() else base.with_name(base.name + ".flac")
        out.append({
            "id": msg_id,
            "recorded_at": meta.get("recorded_at", ""),
            "duration_s": meta.get("duration_s"),
            "title": meta.get("title") or "",
            "notes": meta.get("notes") or "",
            "people": meta.get("people") or [],
            "tags": meta.get("tags") or [],
            "favorite": bool(meta.get("favorite")),
            "kind": meta.get("kind") or "guest",
            "transcript": transcript.read_text().strip() if transcript else "",
            "audio": archive.rel(audio),
            "flac": archive.rel(base.with_name(base.name + ".flac")),
        })
    out.sort(key=lambda m: m["id"])
    return out


def render(messages: list[dict], *, generated_at: str | None = None) -> str:
    e = html.escape
    now = datetime.now().astimezone()
    generated_at = generated_at or f"{MONTHS[now.month - 1]} {now.day}, {now.year}"
    total_s = sum(float(m["duration_s"] or 0) for m in messages)
    length = f"{total_s / 3600:.1f} hours" if total_s >= 3600 else f"{round(total_s / 60)} minutes"
    years = sorted({m["id"][:4] for m in messages}, reverse=True)

    parts: list[str] = []
    month = None
    for m in reversed(messages):  # newest first
        label = f"{MONTHS[int(m['id'][5:7]) - 1]} {m['id'][:4]}"
        if label != month:
            if month is not None:
                parts.append("</section>")
            anchor = f' id="y{m["id"][:4]}"' if month is None or month[-4:] != m["id"][:4] else ""
            parts.append(f'<section><h2{anchor}>{e(label)}</h2>')
            month = label
        date, time = _when(m["recorded_at"])
        title = m["title"] or date
        meta = (f"{e(date)} · " if m["title"] else "") + f"{e(time)} · {_duration(m['duration_s'])}"
        chips = "".join(f'<span class="chip">{e(p)}</span>' for p in m["people"])
        chips += "".join(f'<span class="chip">#{e(t)}</span>' for t in m["tags"])
        search = " ".join([title, date, m["notes"], m["transcript"], *m["people"], *m["tags"]]).lower()
        parts.append(
            f'<article data-s="{e(search)}">'
            f'<h3>{"★ " if m["favorite"] else ""}{e(title)}</h3>'
            f'<p class="meta">{meta}</p>'
            f'<audio controls preload="none" src="{e(m["audio"])}"></audio>'
            + (f'<p class="chips">{chips}</p>' if chips else "")
            + (f'<p class="notes">{e(m["notes"])}</p>' if m["notes"] else "")
            + (f'<details><summary>Transcript</summary><p class="t">{e(m["transcript"])}</p></details>'
               if m["transcript"] else "")
            + f'<p class="files"><a href="{e(m["flac"])}">original</a> · <code>{e(m["id"])}</code></p>'
            "</article>")
    if month is not None:
        parts.append("</section>")
    body = "\n".join(parts) or '<p class="empty">No messages yet.</p>'
    jump = " ".join(f'<a href="#y{y}">{y}</a>' for y in years)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="PhoneHome {e(__version__)}">
<title>PhoneHome archive</title>
<style>
  :root {{ --bg:#f6f3ee; --fg:#2b2622; --muted:#7a716a; --card:#fffdf9; --line:#e4ddd4; --accent:#b5452f; --chip:#efe8df; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#1d1a18; --fg:#eee6dd; --muted:#a39a91; --card:#272320; --line:#3a3430; --accent:#e0735a; --chip:#342f2b; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  main {{ max-width:760px; margin:0 auto; padding:24px 16px 60px; }}
  header p {{ color:var(--muted); margin:4px 0 0; }}
  h1 {{ margin:0; font-size:1.7rem; }}
  h2 {{ font-size:.8rem; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); margin:28px 0 8px; }}
  h3 {{ margin:0; font-size:1.05rem; }}
  .bar {{ position:sticky; top:0; background:var(--bg); padding:12px 0; z-index:1; }}
  input {{ width:100%; font:inherit; padding:10px 12px; border-radius:10px; border:1px solid var(--line); background:var(--card); color:var(--fg); }}
  .years {{ margin-top:8px; font-size:.9rem; }}
  .years a {{ color:var(--accent); margin-right:8px; }}
  article {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin:0 0 12px; }}
  .meta {{ color:var(--muted); margin:2px 0 10px; font-size:.9rem; }}
  audio {{ width:100%; height:40px; }}
  .chips {{ margin:8px 0 0; }}
  .chip {{ display:inline-block; background:var(--chip); border-radius:999px; padding:1px 10px; font-size:.85rem; margin:0 4px 4px 0; }}
  .notes {{ margin:8px 0 0; }}
  details {{ margin-top:8px; }} summary {{ cursor:pointer; color:var(--muted); font-size:.9rem; }}
  .t {{ white-space:pre-wrap; margin:6px 0 0; }}
  .files {{ font-size:.8rem; color:var(--muted); margin:8px 0 0; }}
  .files a {{ color:var(--muted); }}
  footer {{ color:var(--muted); font-size:.85rem; margin-top:40px; border-top:1px solid var(--line); padding-top:16px; }}
  .hide {{ display:none; }}
</style>
</head>
<body>
<main>
<header>
  <h1>PhoneHome archive</h1>
  <p>{len(messages)} message{"" if len(messages) == 1 else "s"} · {length} · made {e(generated_at)}</p>
</header>
<div class="bar">
  <input type="search" id="q" placeholder="Search names, words, dates…" aria-label="Search">
  <div class="years">{jump}</div>
</div>
{body}
<footer>
  <p>This page plays the recordings in the <code>messages</code> folder next to it, so keep them together.
  Each message is a set of files named by date and time: <code>.flac</code> is the original recording,
  <code>.mp3</code> a volume-leveled copy, <code>.json</code> its details, <code>.txt</code> the transcript.
  See README.txt for more.</p>
</footer>
</main>
<script>
  // Search: hide the messages that don't contain every word typed.
  var q = document.getElementById('q');
  q.addEventListener('input', function () {{
    var words = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
    document.querySelectorAll('section').forEach(function (s) {{
      var any = false;
      s.querySelectorAll('article').forEach(function (a) {{
        var hit = words.every(function (w) {{ return a.getAttribute('data-s').indexOf(w) >= 0; }});
        a.classList.toggle('hide', !hit);
        any = any || hit;
      }});
      s.classList.toggle('hide', !any);
    }});
  }});
</script>
</body>
</html>
"""


def write_viewer(archive: Archive) -> Path:
    """(Re)write <archive>/index.html. Returns its path."""
    path = archive.root / "index.html"
    write_bytes_atomic(path, render(collect(archive)).encode())
    return path
