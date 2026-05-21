<div align="center">

# Object Talk Pipeline

### Hindi YouTube Shorts in one click — from a single subject to an uploaded Reel, fully hands-off.

<p>
  <img src="https://img.shields.io/badge/license-MIT-emerald?style=for-the-badge" alt="MIT License" />
  <img src="https://img.shields.io/badge/python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/node-20%2B-339933?style=for-the-badge&logo=node.js&logoColor=white" alt="Node 20+" />
  <img src="https://img.shields.io/badge/built%20with-Claude%20Code-DE7356?style=for-the-badge" alt="Built with Claude Code" />
  <a href="https://hjlabs.in"><img src="https://img.shields.io/badge/by-hjLabs.in-4f46e5?style=for-the-badge" alt="hjLabs.in" /></a>
</p>

<p>
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Vite-646CFF?style=flat-square&logo=vite&logoColor=white" alt="Vite" />
  <img src="https://img.shields.io/badge/React-61DAFB?style=flat-square&logo=react&logoColor=black" alt="React" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/Tailwind-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white" alt="Tailwind" />
  <img src="https://img.shields.io/badge/Gemini-8E75B2?style=flat-square&logo=google&logoColor=white" alt="Gemini" />
  <img src="https://img.shields.io/badge/Grok%20Imagine-000000?style=flat-square&logo=x&logoColor=white" alt="Grok Imagine" />
  <img src="https://img.shields.io/badge/YouTube%20Data%20API-FF0000?style=flat-square&logo=youtube&logoColor=white" alt="YouTube Data API" />
  <img src="https://img.shields.io/badge/Playwright-2EAD33?style=flat-square&logo=playwright&logoColor=white" alt="Playwright" />
  <img src="https://img.shields.io/badge/ffmpeg-007808?style=flat-square&logo=ffmpeg&logoColor=white" alt="ffmpeg" />
</p>

<a href="docs/screenshots/01-auto-run.png">
  <img src="docs/screenshots/01-auto-run.png" alt="Auto run landing page" width="900" />
</a>

<sub><i>Pick a subject. Get 5 personified Pixar-style 3D characters talking Hindi to camera, merged into a 50-second YouTube Short, auto-uploaded.</i></sub>

</div>

---

<details>
<summary><b>📑 Table of Contents</b></summary>

- [Live examples](#live-examples--shorts-produced-by-this-pipeline)
- [What it does](#what-it-does)
- [Highlights](#highlights)
- [Quickstart](#quickstart)
- [Architecture](#architecture)
- [API surface](#api-surface)
- [Costs](#costs-rough-per-full-pipeline-run)
- [Notable design decisions](#notable-design-decisions)
- [Troubleshooting](#troubleshooting)
- [License](#license)
- [Contact](#contact)

</details>

---

## Live examples — Shorts produced by this pipeline

<table>
<tr>
  <td align="center" width="33%">
    <a href="https://youtube.com/shorts/AbIxt_bP7FQ"><img src="https://img.youtube.com/vi/AbIxt_bP7FQ/hqdefault.jpg" width="100%" alt="Smart Factory Heroes" /></a><br/>
    <a href="https://youtube.com/shorts/AbIxt_bP7FQ"><b>▶ Smart Factory Heroes</b></a>
  </td>
  <td align="center" width="33%">
    <a href="https://youtube.com/shorts/L_ANLwXPGcM"><img src="https://img.youtube.com/vi/L_ANLwXPGcM/hqdefault.jpg" width="100%" alt="Indian Street Food" /></a><br/>
    <a href="https://youtube.com/shorts/L_ANLwXPGcM"><b>▶ Indian Street Food</b></a>
  </td>
  <td align="center" width="33%">
    <a href="https://youtube.com/shorts/8V-eQM8zLcQ"><img src="https://img.youtube.com/vi/8V-eQM8zLcQ/hqdefault.jpg" width="100%" alt="Breakfast Superfoods" /></a><br/>
    <a href="https://youtube.com/shorts/8V-eQM8zLcQ"><b>▶ Breakfast Superfoods</b></a>
  </td>
</tr>
<tr>
  <td align="center" width="33%">
    <a href="https://youtube.com/shorts/Ns0Y0-VF56c"><img src="https://img.youtube.com/vi/Ns0Y0-VF56c/hqdefault.jpg" width="100%" alt="EV Charging" /></a><br/>
    <a href="https://youtube.com/shorts/Ns0Y0-VF56c"><b>▶ EV Charging</b></a>
  </td>
  <td align="center" width="33%">
    <a href="https://youtube.com/shorts/WVTmfwQ8vDE"><img src="https://img.youtube.com/vi/WVTmfwQ8vDE/hqdefault.jpg" width="100%" alt="Monsoon Snacks" /></a><br/>
    <a href="https://youtube.com/shorts/WVTmfwQ8vDE"><b>▶ Monsoon Snacks</b></a>
  </td>
  <td align="center" width="33%">
    <a href="https://youtube.com/shorts/SS92CmARvn8"><img src="https://img.youtube.com/vi/SS92CmARvn8/hqdefault.jpg" width="100%" alt="Types of Springs" /></a><br/>
    <a href="https://youtube.com/shorts/SS92CmARvn8"><b>▶ Types of Springs</b></a>
  </td>
</tr>
</table>

<p align="center"><sub>🌐 Built by <a href="https://hjlabs.in">hjLabs.in</a> · Founder <a href="https://www.linkedin.com/in/hemang-joshi-046746aa">Hemang Joshi</a></sub></p>

---

## What it does

Each run produces a 50-second YouTube Short composed of **5 ten-second clips**.
Each clip stars a personified 3D character (e.g. "Robotic Arm", "Mango", "PLC
Controller") confronting the viewer in Hindi about a relatable problem in that
domain.

<table align="center">
<tr>
  <td align="center"><b>1. Scripts</b><br/><sub>Gemini text</sub></td>
  <td align="center">→</td>
  <td align="center"><b>2. Images</b><br/><sub>Gemini image</sub></td>
  <td align="center">→</td>
  <td align="center"><b>3. Videos</b><br/><sub>Grok Imagine</sub></td>
  <td align="center">→</td>
  <td align="center"><b>4. Merge</b><br/><sub>ffmpeg</sub></td>
  <td align="center">→</td>
  <td align="center"><b>5. Upload</b><br/><sub>YouTube API</sub></td>
</tr>
</table>

**Two modes:**

| | |
|---|---|
| **⚡ Auto run** | Full pipeline runs end-to-end (~5–7 min per video). |
| **✋ Manual run** | Only scripts auto-generate; you trigger each image / clip / merge / upload step. |

---

## Highlights

### Auto run page

5-step preview, cost/time badges, advanced options (headless + skip-upload + parallel), Gemini-generated idea backlog + live Google Trends curation below.

![Auto run](docs/screenshots/01-auto-run.png)

### Manual run page

Step-by-step mode with auto vs. click stepper, "when to use" callout, example-subject chips.

![Manual run](docs/screenshots/02-manual-run.png)

### Run detail — script editor with image + clip per row

Each of the 5 scripts gets a row showing its image, video, and editable Hindi
text + image prompt side-by-side. Word-count meter enforces the 10s
speakable budget. ↻ regenerate any individual image or clip.

![Run detail](docs/screenshots/03-run-detail.png)
![Script row detail](docs/screenshots/04-script-row-detail.png)

### Merge + Upload

Final actions card with progress indicators, ETA, privacy toggle, copy-URL
button, and post-upload success state with YouTube link.

![Merge + Upload](docs/screenshots/05-merge-upload.png)

### Idea backlog + Live Google Trends

50 seed subjects + Gemini-generated additions, with done/todo filtering. Plus
a Trending panel that pulls **live Google Trends RSS** and uses Gemini to
convert raw news/celebrity trends into Object-Talk-suitable domains. Category
filter (food / tech / health / lifestyle / etc.) is strict.

![Ideas backlog](docs/screenshots/06-ideas-backlog.png)
![Trending panel](docs/screenshots/08-trending.png)

### Settings — configure everything in-app

No file editing needed. The settings page handles Gemini API key + model
selection, YouTube OAuth client secret upload, and Grok session status.

![Settings](docs/screenshots/07-settings.png)

---

## Quickstart

### Requirements

- **Python 3.13** (this project uses system pip, not a per-project venv)
- **Node.js 20+** (for the Vite frontend)
- **ffmpeg** on PATH
- **System Chromium build** (auto-downloaded by CloakBrowser on first run)
- A **Gemini API key** ([free at Google AI Studio](https://aistudio.google.com/app/apikey))
- A Google Cloud project with **YouTube Data API v3** enabled and an OAuth
  Desktop client (for uploads — optional)
- A **Grok / X account** with Imagine access (for video generation)

### Install

```bash
git clone https://github.com/hemangjoshi37a/object-talk-pipeline.git
cd object-talk-pipeline/pipeline

# Python deps
pip3.13 install requests playwright google-auth google-auth-oauthlib \
                google-api-python-client cloakbrowser fastapi 'uvicorn[standard]'

# Frontend deps
cd web && npm install && cd ..

# Stealth Chromium (first run auto-downloads ~200MB)
python3.13 -c "import cloakbrowser; cloakbrowser.ensure_binary()"
```

### Run

```bash
./run.sh          # starts both backend (:8765) and frontend (:5180), Ctrl+C stops both
```

Open **http://localhost:5180/** and click **Settings** to configure your keys.

`run.sh` subcommands: `up` (default), `stop`, `status`, `restart`.

### Configure keys in the UI

1. Open the app → **Settings** (sidebar)
2. Paste your **Gemini API key**, pick text/image models
3. Upload your **YouTube OAuth client_secret.json** (if you want to upload to YouTube)
4. From a terminal, run `python3.13 steps/grok_session.py` once to log into Grok
5. Done — go to **Auto run** or **Manual run** and pick a subject

---

## Architecture

```
pipeline/
├── webapp.py                 # FastAPI backend
├── run.sh                    # start/stop both servers
├── pipeline.py               # CLI orchestrator (auto run = subprocess of this)
├── config.py                 # loads .env, exposes paths + model IDs
├── prompts/
│   └── object_talk_system.md # the Pixar-3D + 5-beat Hindi system prompt
├── steps/
│   ├── generate_scripts.py   # Gemini text → 5 scripts JSON
│   ├── generate_images.py    # Gemini image → 5 PNG/JPGs at 9:16
│   ├── generate_videos.py    # CloakBrowser+Playwright → 5 MP4s via Grok
│   ├── merge_videos.py       # ffmpeg concat
│   ├── upload_video.py       # YouTube Data API v3 upload
│   ├── grok_session.py       # one-time interactive Grok login helper
│   └── inspect_grok_ui.py    # DOM-inspector for adapting to UI changes
└── web/                      # Vite + React + TS + Tailwind frontend
    └── src/components/
        ├── Sidebar.tsx
        ├── NewRunForm.tsx     ManualRunForm.tsx
        ├── RunView.tsx        StepBar.tsx
        ├── ScriptsEditor.tsx  ArtifactsPanel.tsx
        ├── LogPanel.tsx
        ├── IdeasPanel.tsx     TrendingPanel.tsx
        └── Settings.tsx
```

### Backend event model

One `RunBus` per run-id. Multiple `Job`s (primary pipeline + auxiliary
per-item regens) all publish events into the same bus. SSE subscribers see
interleaved logs from every concurrent subprocess.

### API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/runs` | List runs (filesystem + active) |
| GET | `/api/runs/{id}` | Run state + log tail |
| POST | `/api/runs` | Start auto run |
| POST | `/api/runs/manual` | Start manual (scripts only) |
| POST | `/api/runs/{id}/cancel` | Kill subprocess |
| POST | `/api/runs/{id}/retry` | Retry from a step |
| DELETE | `/api/runs/{id}` | Delete run + files |
| GET | `/api/runs/{id}/events` | **SSE event stream** |
| GET | `/files/{id}/{name}` | Serve generated artifacts |
| GET/PUT | `/api/runs/{id}/scripts` | Read/edit scripts.json |
| POST | `/api/runs/{id}/regen/scripts` | Regenerate scripts |
| POST | `/api/runs/{id}/regen/image/{idx}` | Regenerate single image (concurrent allowed) |
| POST | `/api/runs/{id}/regen/video/{idx}` | Regenerate single clip |
| POST | `/api/runs/{id}/merge` | Manual merge |
| POST | `/api/runs/{id}/upload` | Manual YouTube upload |
| GET/PUT | `/api/settings/...` | In-app settings |
| POST | `/api/trending` | Live Google Trends → curated subjects |
| POST | `/api/ideas/generate` | Gemini-generated subject ideas |

---

## Costs (rough, per full pipeline run)

| Item | Cost |
|---|---|
| Gemini text (scripts + metadata + trending) | ~$0.01 |
| Gemini image (5× 9:16) | ~$0.20 |
| Grok video gen | covered by your Premium subscription |
| YouTube quota | 1,600 / 10,000 daily units per upload (~6 uploads/day) |

---

## Notable design decisions

- **No per-project venv** — uses system `python3.13` + `pip3.13`. Keeps the
  setup portable; no activation step.
- **No official Google Trends API exists** — we pull the
  semi-official RSS at `https://trends.google.com/trending/rss?geo=<CC>` and
  pipe it through Gemini to convert celebrity/news trends into Object-Talk
  *domain* subjects.
- **CloakBrowser, not vanilla Playwright Chromium** — Grok has Cloudflare /
  bot detection. CloakBrowser ships a stealth-patched Chromium that passes
  the usual detection tests. We also use humanized typing (variable delays,
  occasional pauses) and pre-click mouse jitter.
- **File-based lock on the Grok profile** — only one Chrome instance can use
  a user_data_dir at a time. We acquire `pipeline/browser_data/grok.lock`
  with `fcntl.flock` before launching, so concurrent runs queue politely
  instead of crashing with `ProcessSingleton`.
- **Auxiliary jobs for concurrent image regen** — image generations bypass
  the per-run primary-job lock and stream logs into the same RunBus, so 5
  parallel image regens interleave their output in one log panel.

---

## Troubleshooting

- **Gemini script generation fails after retries** — the 40-word Hindi cap is
  strict. Edit `MAX_WORDS` in `steps/generate_scripts.py` (currently 45) or
  loosen the system prompt at `prompts/object_talk_system.md`.
- **Grok video gen times out / Cloudflare challenge** — open
  `http://localhost:5180/`, watch the headed browser (don't tick "headless"),
  solve any challenge once, future runs will reuse the session.
- **YouTube `403 access_denied`** — your OAuth consent screen is in
  Production without verification. Switch back to **Testing** mode in Google
  Cloud Console and add yourself as a Test user.
- **YouTube `Temporary failure in name resolution`** — local DNS / network
  hiccup; not a code bug. Retry when connectivity recovers.
- **Backend can't pick up new .env values** — `setdefault` semantics meant
  the running process kept the old key. Since the in-app Settings page now
  directly writes `os.environ` *and* spawns new subprocesses with the
  current env, this is resolved. For deep changes, restart with `./run.sh restart`.

---

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- The **Object Talk 2.0 (Hindi)** content format inspiration
- **Grok Imagine** for image-to-video generation
- **Google Gemini** for text + image generation
- **CloakBrowser** for stealth Chromium
- **xAI / Google Trends RSS** for live trending data

---

## Contact

**Hemang Joshi** -- Founder, [hjLabs.in](https://hjlabs.in)

[![Email](https://img.shields.io/badge/Email-hemangjoshi37a@gmail.com-EA4335?style=for-the-badge&logo=gmail&logoColor=white)](mailto:hemangjoshi37a@gmail.com)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Hemang_Joshi-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/hemang-joshi-046746aa)
[![YouTube](https://img.shields.io/badge/YouTube-@HemangJoshi-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@HemangJoshi)
[![WhatsApp](https://img.shields.io/badge/WhatsApp-+91_7016525813-25D366?style=for-the-badge&logo=whatsapp&logoColor=white)](https://wa.me/917016525813)
[![Telegram](https://img.shields.io/badge/Telegram-@hjlabs-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/hjlabs)

**hjLabs.in** -- Industrial Automation | AI/ML | IoT | SEO Tools

Serving **15+ countries** with a **4.9 Google rating**

[![Website](https://img.shields.io/badge/%F0%9F%8C%90_hjLabs.in-Visit_Website-4f46e5?style=for-the-badge)](https://hjlabs.in)

---
