# phonehome-web

The PhoneHome web app: SvelteKit 3 + Svelte 5, built to static files that the server
hosts. There's no Node in production: the Docker image builds this once and serves the
result.

| Page | What it's for |
|---|---|
| **Messages** | Timeline, newest first. One-tap play on every card, "new" dots, favorites, and an *On this day* banner |
| **Message** | Waveform player. The transcript highlights each word as it plays; tap a word to jump there. Correct the transcript, edit title/people/tags/notes, download, trash |
| **Search** | Full-text search over what people said, plus titles, notes and names. Filter by year, person, tag, kind and favorites. Matches are highlighted |
| **Memories** | Messages from this date in earlier years. Browse any other day too |
| **Greetings** | Greetings and prompts: upload or record in the browser, preview, switch on and off, and set when each plays (dates, weekdays, times, priority, rotation share). Shows what plays right now |
| **Phone** | Is it online, what it's doing, Ring now, test tones, restart, log |
| **Settings** | Recording limits, the phone's full settings JSON, transcription status, accounts |

Works on a phone (bottom tab bar) and in dark mode, and can be added to a home screen.

## Develop

Run a PhoneHome server on :8080 (see [server/README.md](../server/README.md)), then:

```bash
cd web
npm ci
npm run dev
```

Open <http://localhost:5173>. API calls are proxied to the server; set `PHONEHOME_SERVER`
to point somewhere else. Recording greetings in the browser works on `localhost`
(browsers only allow microphones on HTTPS or localhost).

## Build

```bash
npm run build      # → web/build/
```

The server serves `web/build` automatically when run from the repository.

## Notes for future changes

- SvelteKit 3 keeps its config in `vite.config.js` (no `svelte.config.js`), and `#lib/...`
  imports come from the `imports` map in `package.json`.
- Every API call goes through `src/lib/api.js`, which adds the `X-Requested-With` header the
  server requires on changes, and sends you to the login page on a 401.
- Dates are shown in the phone's local time as recorded (the timestamp's own offset), not
  the viewer's time zone (`src/lib/format.js`).
- Dependencies are pinned to exact versions so a rebuild years from now produces the same app.
