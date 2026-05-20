# Object-Talk Hindi Reels Pipeline

End-to-end automation: subject → 5 scripts → 5 images → 5 Grok videos → merged → YouTube Short.

## One-time setup

Deps are installed system-wide for `python3.13` (already done):

```bash
pip3.13 install requests playwright google-auth google-auth-oauthlib google-api-python-client cloakbrowser
```

Browser: CloakBrowser ships a stealth-patched Chromium that's auto-installed at `~/.cloakbrowser/chromium-*/chrome`. No Playwright `chromium install` needed.

Confirm `.env` has your `GEMINI_API_KEY` (already populated).

### Log into Grok (one time)

The video generation step uses your real Grok account via Playwright. Run the helper once:

```bash
python3.13 steps/grok_session.py
```

A Chrome window opens at `grok.com/imagine`. Sign in normally. When you can see the Imagine UI working, return to the terminal and press Enter. The session is saved to `browser_data/grok/` and reused on every subsequent run for ~weeks until cookies expire.

### YouTube OAuth

Already set up — token cached at `~/.youtube-mcp/token.json`. If it expires the upload step re-prompts a browser flow.

## Per-run usage

Once Grok is logged in:

```bash
python3.13 pipeline.py "smart factory automation"
```

This runs all steps in sequence and uploads the final video. Outputs land in `output/<slug>/`:

- `scripts.json` — 5 image prompts + Hindi scripts
- `img_NN_<object>.jpg` — 5 images (9:16, ~768×1376)
- `vid_NN_<object>.mp4` — 5 Grok video clips
- `merge.mp4` — concatenated final
- `metadata.json` — auto-generated YouTube title/description/tags
- `youtube_url.txt` — uploaded video URL

## Individual steps

```bash
python3.13 steps/generate_scripts.py "subject"
python3.13 steps/generate_images.py output/<slug>/scripts.json
python3.13 steps/generate_videos.py output/<slug>/    # uses Grok
python3.13 steps/merge_videos.py output/<slug>/
python3.13 steps/upload_video.py output/<slug>/merge.mp4 output/<slug>/scripts.json
```

## Costs per run (approximate)

- Gemini text (scripts + metadata): ~$0.01
- Gemini image (5× `gemini-3-pro-image-preview`): ~$0.20
- Grok: covered by Premium subscription
- YouTube quota: 1,600 / 10,000 daily units per upload (≈ 6 uploads/day)

## Web UI (Vite + React frontend, FastAPI backend)

Full dynamic web app at `web/` with run history, live progress streaming over SSE, step indicator, artifact gallery (images + video previews), and process controls (cancel / retry-from-step / delete).

### Production mode (recommended — single command)

```bash
cd web && npm run build && cd ..
python3.13 webapp.py
```

Open http://localhost:8765. FastAPI serves both the API and the built frontend.

### Dev mode (hot-reload frontend)

```bash
# terminal 1 — backend (port 8765)
python3.13 webapp.py

# terminal 2 — Vite dev server (port 5173, proxies /api + /files to backend)
cd web && npm run dev
```

Open http://localhost:5173. Frontend hot-reloads on edits.

### API endpoints (in case you want to script around it)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/runs` | List all runs (filesystem + active jobs) |
| GET | `/api/runs/{id}` | Full state of one run |
| POST | `/api/runs` | Start a new run, body: `{subject, privacy, headless, skip_upload}` |
| POST | `/api/runs/{id}/cancel` | Kill the subprocess |
| POST | `/api/runs/{id}/retry` | Re-run from a step, body: `{from_step}` |
| DELETE | `/api/runs/{id}` | Delete the run and all its files |
| GET | `/api/runs/{id}/events` | SSE stream: `{kind, payload}` events for `log` / `step` / `progress` / `artifact` / `youtube` / `status` |
| GET | `/files/{id}/{name}` | Serve generated images / videos / json |

## Resume after failure

The orchestrator is resumable — each step skips if its output already exists. To force a specific step to re-run:

```bash
python3.13 pipeline.py "subject" --from-step videos    # re-do videos onward
python3.13 pipeline.py "subject" --from-step upload    # just re-upload (uses existing merge.mp4)
```

To retry a single failed video clip without redoing everything:

```bash
python3.13 steps/generate_videos.py output/<slug>/ --only 3    # just video #3
```

## Troubleshooting

- **CloakBrowser binary missing**: `python3.13 -c "import cloakbrowser; cloakbrowser.ensure_binary()"` (or download `cloakbrowser-linux-x64.tar.gz` from the [GitHub releases](https://github.com/CloakHQ/cloakbrowser/releases) and extract to `~/.cloakbrowser/chromium-<version>/`).
- **Grok cookie expired**: re-run `steps/grok_session.py` to refresh.
- **Script over 40 words**: handled — `generate_scripts.py` retries up to 3 times with corrective feedback.
- **YouTube 403 access_denied**: your OAuth consent screen is "In production" without verification. Switch back to "Testing" and add yourself as Test user.
- **Grok UI changed (selectors broken)**: re-run `python3.13 steps/inspect_grok_ui.py` to dump the live DOM to `/tmp/grok_inspect/`, then update selectors in `steps/generate_videos.py`.
