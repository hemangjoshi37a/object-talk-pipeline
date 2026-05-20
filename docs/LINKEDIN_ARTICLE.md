# I Built an Open-Source Pipeline That Turns One Sentence Into a Finished Hindi YouTube Short in 5 Minutes

![Object Talk Pipeline cover image — open-source AI pipeline that generates Hindi YouTube Shorts end-to-end using Google Gemini 3 Pro Image Preview, Grok Imagine, FastAPI, Playwright and YouTube Data API v3](screenshots/linkedin-cover.png)

## The Indian short-form content problem nobody is fixing

A creator friend in Pune ships one Hindi YouTube Short per week. Scripting, voiceover, B-roll, vertical cuts, three title experiments, upload. Nine hours per Short. He has a day job.

That math is broken. English creators have a hundred AI tools tuned for them. Hindi, Tamil, Marathi creators still hand-stitch Premiere timelines because the AI tooling stack assumes English scripts, English voices, English-trained image models.

So I built [**Object Talk Pipeline**](https://github.com/hemangjoshi37a/object-talk-pipeline) — an MIT-licensed, end-to-end system that takes a single subject like *"smart factory automation"* and produces a finished, uploaded 50-second Hindi YouTube Short in about five minutes. Fully hands-off. Runs on a laptop. Here's the engineering writeup.

## What the pipeline actually does

You type a subject into a Vite + React UI. Five minutes later, a 9:16 Hindi Short is live on your channel with a Gemini-written title, description and tags.

Every Short follows the same dramaturgical formula:

- **Five clips, ten seconds each, totalling fifty seconds** (the optimum Shorts watch-time window)
- Each clip features a **Pixar-style 3D character that personifies an object** in the subject domain
- For *"smart factory automation"*, the cast becomes a Robotic Arm, an Industrial Sensor, a PLC Controller, a Conveyor Belt and a Quality-Check Camera
- Every character looks at the camera and speaks **Hindi**, budgeted to ≤45 words per 10-second clip
- Each character delivers a first-person gripe about their job — relatable, slightly funny, always topical

Six unedited outputs from this pipeline, all uploaded automatically by the same code:

- Smart factory automation — [youtube.com/shorts/L_ANLwXPGcM](https://youtube.com/shorts/L_ANLwXPGcM)
- Industrial IoT — [youtube.com/shorts/8V-eQM8zLcQ](https://youtube.com/shorts/8V-eQM8zLcQ)
- Robotic process automation — [youtube.com/shorts/Ns0Y0-VF56c](https://youtube.com/shorts/Ns0Y0-VF56c)
- Predictive maintenance — [youtube.com/shorts/WVTmfwQ8vDE](https://youtube.com/shorts/WVTmfwQ8vDE)
- Warehouse robotics — [youtube.com/shorts/SS92CmARvn8](https://youtube.com/shorts/SS92CmARvn8)
- Computer vision in manufacturing — [youtube.com/shorts/AbIxt_bP7FQ](https://youtube.com/shorts/AbIxt_bP7FQ)

Full source on GitHub: [github.com/hemangjoshi37a/object-talk-pipeline](https://github.com/hemangjoshi37a/object-talk-pipeline).

## Architecture at a glance

Four AI calls glued together by a FastAPI orchestrator and a Server-Sent Events log stream.

- **Gemini 3.5 Flash** writes 5 image prompts + 5 Hindi scripts, ≤45-word-per-clip budget enforced in the prompt itself
- **Gemini 3 Pro Image Preview** renders 5 Pixar-style 9:16 stills
- **Grok Imagine** (driven through a stealth Playwright browser) turns each still into a 10-second 720p video
- **ffmpeg** concatenates the five clips into one MP4
- **Gemini again** writes the YouTube title, description, and tag set
- **YouTube Data API v3** uploads via stored OAuth credentials
- The **React + Tailwind dark-zinc UI** subscribes to the SSE feed and renders every subprocess log live

Per-module breakdown and the architecture diagram live in the [project README on GitHub](https://github.com/hemangjoshi37a/object-talk-pipeline).

## The four hardest engineering problems I solved

The "easy demo" version of any pipeline is misleading. The first version of this one was a Jupyter notebook that worked exactly once. Turning that into something a stranger can clone and trust took me through four real problems.

### 1. One SSE stream, many concurrent subprocesses

The pipeline isn't a single script. It spawns subprocesses for image generation, video generation, ffmpeg, and YouTube upload. Users routinely fire **image regens for individual clips in parallel** while the main job is still running. Five regens, one primary run, all writing logs at once.

The naive answer — one SSE endpoint per job — breaks the moment a user opens a second tab.

I built a per-run **RunBus**: a shared async event bus keyed on `run_id`. Every subprocess writes through a tagged stdout adapter (`[pipeline]`, `[regen-img-3]`, `[upload]`). The UI subscribes once and renders interleaved output, exactly like `docker-compose up`. Auxiliary jobs — the parallel image regens — bypass the primary-job lock and still share the bus. [Implementation on GitHub](https://github.com/hemangjoshi37a/object-talk-pipeline).

### 2. There is no official Google Trends API — so I built one

The "Trending Now" button is the feature people ask about most. It pulls the **semi-official Google Trends RSS feed for India**, parses the top 20 rising queries, and pipes each one through Gemini with a domain-classifier prompt: *"Is this an industry, hobby, or technology? If yes, rewrite it as an Object-Talk subject. If it's pure gossip or politics, return null."*

*"Monsoon farming"* gets converted to *"smart irrigation systems"* with five characters already implied. *"Actress X breakup"* gets dropped on the floor. The user sees a curated list of usable subjects, refreshed live.

Full extractor in the [Object Talk Pipeline repo](https://github.com/hemangjoshi37a/object-talk-pipeline).

### 3. Driving Grok Imagine without getting bot-banned

This was the worst week of the project.

Grok Imagine has no public API. Cloudflare on grok.com is aggressive — vanilla Playwright eats a CAPTCHA wall in under 30 seconds.

What worked, in order of impact:

- **CloakBrowser** wrapper around Playwright with realistic fingerprints (WebGL vendor, timezone, font enumeration, navigator props)
- **Humanized typing**: per-character delays uniformly sampled between 25 and 90 ms, longer pauses after `,` and `.`, and a 200–400 ms blink before pressing Enter
- **Mouse jitter** before every click — a 3–8 pixel random walk to the target so the cursor never teleports
- **Session reuse** through a persistent Chrome profile so the bot doesn't re-authenticate every run

Together these dropped the CAPTCHA rate from ~40% to under 2% across a 100-run test. The [stealth automation module](https://github.com/hemangjoshi37a/object-talk-pipeline) is one of the most generally useful pieces in this repo if you do any kind of browser-driven AI tool integration.

### 4. The Chrome profile lock problem

Once humans started using the UI, they did the obvious thing: kicked off two video generations at once.

Chrome refuses to launch a second instance against the same user-data-dir and throws the famously cryptic `ProcessSingleton: Failed to create SingletonLock`. The first version of the pipeline crashed.

Fix: I wrapped the profile with an **`fcntl.flock` file-based lock**. The second concurrent run blocks on the lock, surfaces *"queued behind run #42"* into the SSE log, and starts the instant the first run releases. No race conditions. No Redis. Twenty lines of code. Best ROI in the codebase.

## What it costs to run

- **₹8–₹12 in API spend** per finished Short (Gemini + Grok Imagine credits)
- **~5 minutes of wall-clock time**, fully unattended
- **Zero cloud bill** — the orchestrator runs locally
- **One sentence of human input**

A creator who used to spend 9 hours per Short can now ship one a day. That's the unlock.

## How to run it locally

Designed for a developer laptop. From a clean clone:

```
git clone https://github.com/hemangjoshi37a/object-talk-pipeline
cd object-talk-pipeline
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
python -m backend.main          # FastAPI on :8000
cd frontend && npm run dev      # Vite on :5180
```

Open `http://localhost:5180`, click **Settings** in the sidebar, paste your Gemini API key, drop in your YouTube OAuth `client_secret.json`, pick a Gemini model, and you're live. No `.env` editing. No config-file archaeology. OAuth token status sits at the top of the page so you know the instant a refresh expires.

Full setup guide in the [GitHub README](https://github.com/hemangjoshi37a/object-talk-pipeline).

## Open source, MIT licensed, take it apart

The license is permissive on purpose. Fork it. Ship it.

- Swap Hindi for Tamil, Marathi, Bengali — one prompt template
- Replace Grok Imagine with Veo 3 or Runway — one driver module
- Plug in a different LLM backend — Gemini calls sit behind a thin adapter
- Use it for landscape YouTube or Instagram Reels — flip one ffmpeg flag

Pull requests welcome. Issues are open. The code is intentionally readable — no clever abstractions, no premature DRY, no framework worship.

Repo: [github.com/hemangjoshi37a/object-talk-pipeline](https://github.com/hemangjoshi37a/object-talk-pipeline)

## What's next on the roadmap

The pipeline shipped, but it's a starting point, not a finish line. The next milestones I'm working on:

- **Voiceover-first mode** — currently the characters speak via the video model's lip-sync; an ElevenLabs Hindi voice clone path is in a feature branch
- **Multi-region trends** — Maharashtra-only, Tamil Nadu-only, Karnataka-only RSS slices so the trend mixer matches the audience language
- **A/B title testing** — Gemini generates three title variants, the pipeline ships the first one, the analytics module rotates titles every 6 hours based on CTR pulled from the YouTube Analytics API
- **Cost dashboard** — per-Short Gemini + Grok spend tracked in the UI, so creators see ROI in real time

If any of those overlap with what you need, the [issues tab on GitHub](https://github.com/hemangjoshi37a/object-talk-pipeline) is where the conversation happens.

## About the author

I'm **Hemang Joshi**, founder of [hjLabs](https://hjlabs.in). I ship production AI systems — agentic pipelines, RAG, computer vision, and the unglamorous browser automation that holds it all together. Object Talk Pipeline is the kind of system hjLabs delivers for clients in 2–4 weeks; this is the open-source distillation.

If you want help shipping something like this inside your company, or an agentic AI audit on systems you already run, my booking link is [cal.com/hemangjoshi37a](https://cal.com/hemangjoshi37a).

Star the repo if it's useful. Open an issue if it's broken. Tell me what you build with it.

---

HASHTAGS: #OpenSource #GenerativeAI #AgenticAI #YouTubeShorts #IndianStartups #Gemini #Grok #Playwright #FastAPI #React #HindiContent #AIAutomation #ContentCreation #MLOps #BuildInPublic
