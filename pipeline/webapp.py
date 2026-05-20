"""FastAPI backend for the Object Talk pipeline web UI.

Endpoints:
  GET    /api/runs                       — list all runs (filesystem + active)
  GET    /api/runs/{run_id}              — full state of one run
  POST   /api/runs                       — start a new run
  POST   /api/runs/{run_id}/cancel       — kill the subprocess
  POST   /api/runs/{run_id}/retry        — start a new run forced from a specific step
  DELETE /api/runs/{run_id}              — delete the run's output directory
  GET    /api/runs/{run_id}/events       — SSE stream of progress events
  GET    /files/{run_id}/{filename}      — serve artifact files

Run:
  python3.13 webapp.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
WEB_DIST = ROOT / "web" / "dist"
PIPELINE_SCRIPT = ROOT / "pipeline.py"
PYTHON = sys.executable or "python3.13"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STEP_ORDER = ["scripts", "images", "videos", "merge", "upload"]
STEP_FROM_HEADER = {  # parses lines like ">>> Step 1/5: generate scripts"
    1: "scripts", 2: "images", 3: "videos", 4: "merge", 5: "upload",
}

# ---------- Run bus (shared event stream per run_id) ----------

class RunBus:
    """Per-run-id shared state: event stream, log buffer, primary status.
    Multiple Jobs can publish into the same bus — pipeline run + concurrent
    image/video regens all share one stream so the UI sees ALL of their logs.
    """

    def __init__(self, run_id: str, loop: asyncio.AbstractEventLoop):
        self.run_id = run_id
        self.loop = loop
        self.events: list[dict] = []
        self.log: list[str] = []
        self.status = "idle"  # running | done | error | cancelled | idle
        self.current_step: str | None = None
        self.step_progress: dict | None = None
        self.youtube_url: str | None = None
        self.subject: str | None = None
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.condition = asyncio.Condition()
        self._lock = threading.Lock()
        self.active_jobs: int = 0  # how many Jobs currently feeding this bus

    def emit(self, kind: str, payload: Any) -> None:
        event = {"kind": kind, "payload": payload}
        with self._lock:
            self.events.append(event)
            self.updated_at = time.time()
            if kind == "log":
                self.log.append(payload)
                if len(self.log) > 2000:
                    self.log = self.log[-2000:]
        asyncio.run_coroutine_threadsafe(self._notify(), self.loop)

    async def _notify(self) -> None:
        async with self.condition:
            self.condition.notify_all()


BUSES: dict[str, RunBus] = {}


def get_bus(run_id: str, subject: str | None = None) -> RunBus:
    bus = BUSES.get(run_id)
    if bus is None:
        bus = RunBus(run_id, asyncio.get_running_loop())
        BUSES[run_id] = bus
    if subject and not bus.subject:
        bus.subject = subject
    return bus


# ---------- Job runtime state ----------

class Job:
    """A single subprocess feeding events into its run_id's RunBus.

    primary=True: this job owns the run's "status / current_step / progress"
      (e.g. the full pipeline, a manual scripts gen, a merge, an upload).
    primary=False (auxiliary): a fire-and-forget regen that only streams logs
      and lets the artifact scanner detect file appearance. Does NOT overwrite
      the bus's status — so it can coexist with a primary job in flight.
    """

    def __init__(self, run_id: str, cmd: list[str], *, primary: bool = True,
                 label: str | None = None, subject: str | None = None):
        self.run_id = run_id
        self.cmd = cmd
        self.primary = primary
        self.label = label  # e.g. "regen-image-3", used to prefix log lines
        self.bus = get_bus(run_id, subject)
        self.proc: subprocess.Popen | None = None
        self.is_active = False

    @property
    def status(self) -> str:
        return self.bus.status if self.primary else ("running" if self.is_active else "done")

    @property
    def subject(self) -> str:
        return self.bus.subject or self.run_id

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=True,
        )
        self.is_active = True
        with self.bus._lock:
            self.bus.active_jobs += 1
        if self.primary:
            self.bus.status = "running"
            self.bus.emit("status", "running")
        threading.Thread(target=self._reader, daemon=True).start()
        # One artifact scanner per RUN — start only if not already running.
        # We just spawn one per Job; cheap and ends with the job.
        threading.Thread(target=self._artifact_scanner, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        prefix = "" if self.primary or not self.label else f"[{self.label}] "
        for raw in self.proc.stdout:
            line = raw.rstrip("\n")
            self.bus.emit("log", prefix + line)
            if self.primary:
                self._parse_line(line)
        self.proc.wait()
        self.is_active = False
        with self.bus._lock:
            self.bus.active_jobs = max(0, self.bus.active_jobs - 1)
        if self.primary and self.bus.status == "running":
            new_status = "done" if self.proc.returncode == 0 else "error"
            self.bus.status = new_status
            self.bus.emit("status", new_status)

    def _parse_line(self, line: str) -> None:
        bus = self.bus
        m = re.search(r">>> Step (\d+)/5:", line)
        if m:
            bus.current_step = STEP_FROM_HEADER.get(int(m.group(1)))
            bus.emit("step", bus.current_step)
            return
        m = re.search(r"--- Step (\d+)/5", line)
        if m:
            bus.current_step = STEP_FROM_HEADER.get(int(m.group(1)))
            bus.emit("step", bus.current_step)
            return
        m = re.match(r"\[(\d+)/5\]\s", line)
        if m:
            done = int(m.group(1)) - 1
            bus.step_progress = {
                "step": bus.current_step or "images",
                "done": done,
                "total": 5,
            }
            bus.emit("progress", bus.step_progress)
            return
        m = re.match(r"^\s+(\d{1,3})%\s*$", line)
        if m:
            pct = max(0, min(100, int(m.group(1))))
            bus.step_progress = {"step": "upload", "done": pct, "total": 100}
            bus.emit("progress", bus.step_progress)
            return
        m = re.search(r"(https?://(?:youtu\.be/|www\.youtube\.com/watch\?v=)[\w-]+)", line)
        if m:
            bus.youtube_url = m.group(1)
            bus.emit("youtube", bus.youtube_url)

    def _artifact_scanner(self) -> None:
        last_snapshot: dict | None = None
        while True:
            time.sleep(2)
            snap = artifacts_for(self.run_id)
            if snap != last_snapshot:
                last_snapshot = snap
                self.bus.emit("artifact", snap)
            if not self.is_active and self.proc and self.proc.poll() is not None:
                final = artifacts_for(self.run_id)
                if final != last_snapshot:
                    self.bus.emit("artifact", final)
                break

    def cancel(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.is_active = False
        if self.primary:
            self.bus.status = "cancelled"
            self.bus.emit("status", "cancelled")


# Primary job per run_id (for cancel + 409 protection).
# Auxiliary jobs (image regens) are tracked separately and can be concurrent.
JOBS: dict[str, Job] = {}
AUX_JOBS: dict[str, list[Job]] = {}


# ---------- Filesystem-derived run state ----------

def slug_of(subject: str) -> str:
    return "-".join(subject.lower().split())[:60]


def artifacts_for(run_id: str) -> dict:
    d = OUTPUT_DIR / run_id
    if not d.exists():
        return {
            "scripts_json": None,
            "images": [],
            "videos": [],
            "merged": None,
            "metadata_json": None,
        }
    files = sorted(p.name for p in d.iterdir() if p.is_file())
    def fileurl(name: str) -> str:
        return f"/files/{run_id}/{name}"
    return {
        "scripts_json": fileurl("scripts.json") if "scripts.json" in files else None,
        "metadata_json": fileurl("metadata.json") if "metadata.json" in files else None,
        "images": [fileurl(f) for f in files if f.startswith("img_")],
        "videos": [fileurl(f) for f in files if f.startswith("vid_") and f.endswith(".mp4")],
        "merged": fileurl("merge.mp4") if "merge.mp4" in files else None,
    }


def run_dict(run_id: str) -> dict:
    bus = BUSES.get(run_id)
    d = OUTPUT_DIR / run_id
    scripts_path = d / "scripts.json"
    subject = run_id
    if scripts_path.exists():
        try:
            data = json.loads(scripts_path.read_text())
            subject = data.get("subject", run_id)
        except Exception:
            pass
    if bus and bus.subject:
        subject = bus.subject
    arts = artifacts_for(run_id)
    yt_file = d / "youtube_url.txt"
    youtube_url = bus.youtube_url if bus else None
    if not youtube_url and yt_file.exists():
        youtube_url = yt_file.read_text().strip()
    status = bus.status if bus and bus.status != "idle" else _derived_status(arts, youtube_url)
    # "is_active" reflects whether ANY job (primary or aux) is feeding events.
    is_active = bool(bus and bus.active_jobs > 0)
    created_at = bus.created_at if bus else (d.stat().st_mtime if d.exists() else 0)
    updated_at = bus.updated_at if bus else (d.stat().st_mtime if d.exists() else 0)
    return {
        "id": run_id,
        "subject": subject,
        "status": status,
        "current_step": bus.current_step if bus else None,
        "step_progress": bus.step_progress if bus else None,
        "created_at": created_at,
        "updated_at": updated_at,
        "youtube_url": youtube_url,
        "artifacts": arts,
        "is_active": is_active,
        "log_tail": (bus.log[-300:] if bus else []),
    }


def _derived_status(arts: dict, youtube_url: str | None) -> str:
    if youtube_url:
        return "done"
    if arts["merged"] or arts["videos"]:
        return "done"  # at least partial work survived
    if arts["scripts_json"]:
        return "done"
    return "idle"


# ---------- API models ----------

class RunOptions(BaseModel):
    subject: str
    privacy: str = "public"
    headless: bool = False
    skip_upload: bool = False
    parallel: bool = False


class RetryOptions(BaseModel):
    from_step: str
    privacy: str | None = None
    headless: bool | None = None
    skip_upload: bool | None = None


# ---------- FastAPI ----------

app = FastAPI(title="Object Talk Pipeline")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/runs")
def list_runs() -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for run_id in BUSES:
        out.append(run_dict(run_id))
        seen.add(run_id)
    if OUTPUT_DIR.exists():
        for d in OUTPUT_DIR.iterdir():
            if d.is_dir() and d.name not in seen:
                out.append(run_dict(d.name))
    out.sort(key=lambda r: r["updated_at"], reverse=True)
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    if run_id not in BUSES and not (OUTPUT_DIR / run_id).exists():
        raise HTTPException(404, "not found")
    return run_dict(run_id)


def _build_cmd(opts: RunOptions, from_step: str | None = None) -> list[str]:
    cmd = [PYTHON, "-u", str(PIPELINE_SCRIPT), opts.subject,
           "--privacy", opts.privacy]
    if opts.headless:
        cmd.append("--headless")
    if opts.skip_upload:
        cmd.append("--skip-upload")
    if opts.parallel:
        cmd.append("--parallel")
    if from_step:
        cmd += ["--from-step", from_step]
    return cmd


@app.post("/api/runs")
async def start_run(opts: RunOptions) -> dict:
    run_id = slug_of(opts.subject)
    if run_id in JOBS and JOBS[run_id].is_active:
        raise HTTPException(409, f"already running: {run_id}")
    job = Job(run_id, _build_cmd(opts), primary=True, subject=opts.subject)
    JOBS[run_id] = job
    job.start()
    return run_dict(run_id)


class ManualRunRequest(BaseModel):
    subject: str


@app.post("/api/runs/manual")
async def start_manual_run(req: ManualRunRequest) -> dict:
    """Create a manual run: only generates scripts.json. User triggers everything else
    via the per-item Generate buttons + manual Merge + manual Upload."""
    run_id = slug_of(req.subject)
    if run_id in JOBS and JOBS[run_id].status == "running":
        raise HTTPException(409, f"already running: {run_id}")
    d = OUTPUT_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    cmd = [PYTHON, "-u", str(ROOT / "steps" / "generate_scripts.py"),
           req.subject, "--out", str(d / "scripts.json")]
    _spawn_step_job(run_id, req.subject, cmd, step="scripts")
    return run_dict(run_id)


@app.post("/api/runs/{run_id}/regen/scripts")
async def regen_scripts(run_id: str) -> dict:
    """Re-generate scripts.json for an existing run (keeps subject)."""
    subject = _subject_for(run_id)
    d = OUTPUT_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    cmd = [PYTHON, "-u", str(ROOT / "steps" / "generate_scripts.py"),
           subject, "--out", str(d / "scripts.json")]
    _spawn_step_job(run_id, subject, cmd, step="scripts")
    return run_dict(run_id)


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    job = JOBS.get(run_id)
    if not job:
        raise HTTPException(404, "no active job for this run")
    job.cancel()
    # Also cancel any aux jobs running for this run
    for aux in AUX_JOBS.get(run_id, []):
        if aux.is_active:
            aux.cancel()
    return {"ok": True}


@app.post("/api/runs/{run_id}/retry")
async def retry_run(run_id: str, opts: RetryOptions) -> dict:
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    existing = JOBS.get(run_id)
    if existing and existing.is_active:
        raise HTTPException(409, "still running")
    scripts_path = d / "scripts.json"
    subject = run_id
    if scripts_path.exists():
        try:
            subject = json.loads(scripts_path.read_text()).get("subject", run_id)
        except Exception:
            pass
    privacy = opts.privacy or "public"
    headless = opts.headless if opts.headless is not None else False
    skip_upload = opts.skip_upload if opts.skip_upload is not None else False
    run_opts = RunOptions(subject=subject, privacy=privacy, headless=headless, skip_upload=skip_upload)
    job = Job(run_id, _build_cmd(run_opts, from_step=opts.from_step), primary=True, subject=subject)
    JOBS[run_id] = job
    job.start()
    return run_dict(run_id)


def _spawn_step_job(run_id: str, subject: str, cmd: list[str],
                    step: str | None = None) -> Job:
    """Start a primary subprocess (manual scripts gen, video regen, merge, upload).
    Only one primary job per run_id at a time (Chrome lock + status conflicts)."""
    if run_id in JOBS and JOBS[run_id].is_active:
        raise HTTPException(409, "another job already running for this run")
    job = Job(run_id, cmd, primary=True, subject=subject)
    JOBS[run_id] = job
    job.start()
    if step:
        job.bus.current_step = step
        job.bus.emit("step", step)
    return job


def _spawn_aux_job(run_id: str, cmd: list[str], label: str) -> Job:
    """Start an auxiliary (non-primary) subprocess that shares the run_id's bus.
    Multiple aux jobs can run concurrently (e.g. 5 image regens in parallel).
    Their logs stream to the same SSE stream as the primary job."""
    job = Job(run_id, cmd, primary=False, label=label, subject=_subject_for(run_id))
    AUX_JOBS.setdefault(run_id, []).append(job)
    # Prune dead aux jobs from previous runs
    AUX_JOBS[run_id] = [j for j in AUX_JOBS[run_id] if j.is_active or j is job]
    job.start()
    return job


def _subject_for(run_id: str) -> str:
    p = OUTPUT_DIR / run_id / "scripts.json"
    if p.exists():
        try:
            return json.loads(p.read_text()).get("subject", run_id)
        except Exception:
            pass
    return run_id


@app.post("/api/runs/{run_id}/regen/image/{idx}")
async def regen_image(run_id: str, idx: int) -> dict:
    """Regenerate a single image as an AUX job so multiple regens can run
    concurrently (Gemini API supports parallel — no Chrome lock).
    Logs stream into the run's shared SSE bus so the UI log panel sees them."""
    if idx < 1 or idx > 5:
        raise HTTPException(400, "idx must be 1..5")
    d = OUTPUT_DIR / run_id
    if not (d / "scripts.json").exists():
        raise HTTPException(404, "no scripts.json — generate scripts first")
    cmd = [PYTHON, "-u", str(ROOT / "steps" / "generate_images.py"),
           str(d / "scripts.json"), "--only", str(idx)]
    _spawn_aux_job(run_id, cmd, label=f"regen-img-{idx}")
    return run_dict(run_id)


@app.post("/api/runs/{run_id}/regen/video/{idx}")
async def regen_video(run_id: str, idx: int) -> dict:
    if idx < 1 or idx > 5:
        raise HTTPException(400, "idx must be 1..5")
    d = OUTPUT_DIR / run_id
    if not (d / "scripts.json").exists():
        raise HTTPException(404, "no scripts.json")
    if not list(d.glob(f"img_{idx:02d}_*")):
        raise HTTPException(400, f"no image for index {idx} — generate the image first")
    # Delete the existing video so generate_videos.py doesn't skip it
    for existing in d.glob(f"vid_{idx:02d}_*.mp4"):
        existing.unlink()
    cmd = [PYTHON, "-u", str(ROOT / "steps" / "generate_videos.py"),
           str(d), "--only", str(idx)]
    _spawn_step_job(run_id, _subject_for(run_id), cmd, step="videos")
    return run_dict(run_id)


@app.post("/api/runs/{run_id}/merge")
async def manual_merge(run_id: str) -> dict:
    d = OUTPUT_DIR / run_id
    vids = list(d.glob("vid_*.mp4"))
    if not vids:
        raise HTTPException(400, "no video clips to merge")
    cmd = [PYTHON, "-u", str(ROOT / "steps" / "merge_videos.py"), str(d)]
    _spawn_step_job(run_id, _subject_for(run_id), cmd, step="merge")
    return run_dict(run_id)


class UploadOptions(BaseModel):
    privacy: str = "public"


@app.post("/api/runs/{run_id}/upload")
async def manual_upload(run_id: str, opts: UploadOptions) -> dict:
    d = OUTPUT_DIR / run_id
    merged = d / "merge.mp4"
    scripts = d / "scripts.json"
    if not merged.exists():
        raise HTTPException(400, "merge.mp4 missing — merge first")
    if not scripts.exists():
        raise HTTPException(400, "scripts.json missing")
    cmd = [PYTHON, "-u", str(ROOT / "steps" / "upload_video.py"),
           str(merged), str(scripts), "--privacy", opts.privacy]
    _spawn_step_job(run_id, _subject_for(run_id), cmd, step="upload")
    return run_dict(run_id)


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str) -> dict:
    job = JOBS.get(run_id)
    if job and job.is_active:
        job.cancel()
    for aux in AUX_JOBS.get(run_id, []):
        if aux.is_active:
            aux.cancel()
    JOBS.pop(run_id, None)
    AUX_JOBS.pop(run_id, None)
    BUSES.pop(run_id, None)
    d = OUTPUT_DIR / run_id
    if d.exists():
        shutil.rmtree(d)
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request):
    bus = BUSES.get(run_id)

    async def gen():
        cursor = 0
        if bus:
            # Replay everything in the bus so far
            while cursor < len(bus.events):
                yield f"data: {json.dumps(bus.events[cursor])}\n\n"
                cursor += 1
            # Then stream new events; stay open as long as any job is feeding
            while True:
                if await request.is_disconnected():
                    break
                if cursor < len(bus.events):
                    while cursor < len(bus.events):
                        yield f"data: {json.dumps(bus.events[cursor])}\n\n"
                        cursor += 1
                    continue
                # No new events and no active jobs → close gracefully
                if bus.active_jobs == 0 and bus.status != "running":
                    break
                try:
                    async with bus.condition:
                        await asyncio.wait_for(bus.condition.wait(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        else:
            # No live job — single snapshot of the derived state (don't clobber UI status with 'idle')
            d = OUTPUT_DIR / run_id
            arts = artifacts_for(run_id)
            yt_file = d / "youtube_url.txt"
            yt = yt_file.read_text().strip() if yt_file.exists() else None
            status = _derived_status(arts, yt)
            yield f"data: {json.dumps({'kind': 'status', 'payload': status})}\n\n"
            yield f"data: {json.dumps({'kind': 'artifact', 'payload': arts})}\n\n"
            if yt:
                yield f"data: {json.dumps({'kind': 'youtube', 'payload': yt})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# ---------- Settings (in-app config management) ----------

ENV_FILE = ROOT / ".env"
DEFAULT_YOUTUBE_TOKEN = Path(os.environ.get("YOUTUBE_TOKEN_PATH",
                                            str(Path.home() / ".youtube-mcp" / "token.json")))
DEFAULT_CLIENT_SECRET = Path(os.environ.get("YOUTUBE_CLIENT_SECRET",
                                            str(Path.home() / ".youtube-mcp" / "client_secret.json")))


def _read_env_file() -> dict:
    """Read .env into a dict. Comments + blank lines preserved separately so we
    can rewrite without trashing user-added comments."""
    if not ENV_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _write_env_file(values: dict[str, str]) -> None:
    """Rewrite .env preserving the ordering of any existing keys and appending new ones."""
    existing_order: list[str] = []
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            s = line.strip()
            if "=" in s and not s.startswith("#"):
                k = s.split("=", 1)[0].strip()
                if k not in existing_order:
                    existing_order.append(k)
    # Append any new keys
    for k in values:
        if k not in existing_order:
            existing_order.append(k)
    lines = [f"{k}={values[k]}" for k in existing_order if k in values]
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _mask_secret(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "•" * len(v)
    return v[:4] + "•" * (len(v) - 8) + v[-4:]


@app.get("/api/settings")
def get_settings() -> dict:
    env = _read_env_file()
    gem = env.get("GEMINI_API_KEY", "")
    youtube_token = DEFAULT_YOUTUBE_TOKEN
    client_secret = DEFAULT_CLIENT_SECRET
    grok_profile = Path(env.get("GROK_PROFILE_DIR", str(ROOT / "browser_data" / "grok")))
    return {
        "gemini": {
            "api_key_set": bool(gem),
            "api_key_masked": _mask_secret(gem),
            "text_model": env.get("GEMINI_TEXT_MODEL", "gemini-3.5-flash"),
            "image_model": env.get("GEMINI_IMAGE_MODEL", "gemini-3-pro-image-preview"),
        },
        "youtube": {
            "client_secret_set": client_secret.exists(),
            "client_secret_path": str(client_secret),
            "token_set": youtube_token.exists(),
            "token_path": str(youtube_token),
            "token_age_s": int(time.time() - youtube_token.stat().st_mtime) if youtube_token.exists() else None,
        },
        "grok": {
            "profile_set": grok_profile.exists() and any(grok_profile.iterdir()) if grok_profile.is_dir() else False,
            "profile_path": str(grok_profile),
            "profile_age_s": int(time.time() - grok_profile.stat().st_mtime) if grok_profile.exists() else None,
        },
    }


class GeminiSettingsBody(BaseModel):
    api_key: str | None = None  # if blank, keep existing
    text_model: str | None = None
    image_model: str | None = None


@app.put("/api/settings/gemini")
def put_gemini_settings(body: GeminiSettingsBody) -> dict:
    env = _read_env_file()
    if body.api_key is not None and body.api_key.strip() and not body.api_key.startswith("•"):
        env["GEMINI_API_KEY"] = body.api_key.strip()
        os.environ["GEMINI_API_KEY"] = body.api_key.strip()
    if body.text_model:
        env["GEMINI_TEXT_MODEL"] = body.text_model
        os.environ["GEMINI_TEXT_MODEL"] = body.text_model
    if body.image_model:
        env["GEMINI_IMAGE_MODEL"] = body.image_model
        os.environ["GEMINI_IMAGE_MODEL"] = body.image_model
    _write_env_file(env)
    return get_settings()


@app.get("/api/settings/gemini/models")
def list_gemini_models() -> dict:
    """Live-fetch the model list from Gemini so the Settings dropdowns are up to date."""
    import requests as _requests
    env = _read_env_file()
    key = env.get("GEMINI_API_KEY", "")
    if not key:
        raise HTTPException(400, "Set GEMINI_API_KEY first")
    r = _requests.get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        timeout=20,
    )
    if r.status_code != 200:
        raise HTTPException(502, f"Gemini API: {r.status_code}: {r.text[:200]}")
    data = r.json().get("models", [])
    text_models = []
    image_models = []
    for m in data:
        name = (m.get("name") or "").removeprefix("models/")
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        if "image" in name.lower() or "imagen" in name.lower() or "banana" in name.lower():
            image_models.append(name)
        elif "tts" in name.lower() or "embedding" in name.lower() or "audio" in name.lower():
            continue
        else:
            text_models.append(name)
    return {"text_models": sorted(text_models), "image_models": sorted(image_models)}


@app.post("/api/settings/youtube/client-secret")
async def upload_client_secret(request: Request) -> dict:
    """Accept a raw JSON body (the contents of the OAuth client_secret JSON downloaded
    from Google Cloud Console) and save it to the configured path."""
    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "request body must be valid JSON")
    if "installed" not in data and "web" not in data:
        raise HTTPException(400, "JSON doesn't look like an OAuth client_secret "
                                 "(missing 'installed' or 'web' key)")
    DEFAULT_CLIENT_SECRET.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CLIENT_SECRET.write_text(json.dumps(data))
    DEFAULT_CLIENT_SECRET.chmod(0o600)
    return get_settings()


@app.delete("/api/settings/youtube/token")
def delete_youtube_token() -> dict:
    """Clear cached OAuth token so the next upload triggers a fresh browser consent flow."""
    if DEFAULT_YOUTUBE_TOKEN.exists():
        DEFAULT_YOUTUBE_TOKEN.unlink()
    return get_settings()


class TrendingRequest(BaseModel):
    geo: str = "IN"
    category: str = "any"
    count: int = 10
    refresh: bool = False


_TRENDING_CACHE: dict[str, tuple[float, list, list]] = {}
_TRENDING_CACHE_TTL_S = 3600  # 1 hour


def _fetch_google_trends_rss(geo: str) -> list[dict]:
    """Pull live Google Trends from the (semi-official, no-auth) RSS feed.

    URL: https://trends.google.com/trending/rss?geo=<ISO country code>
    Returns top trending search queries + the news headlines that pushed each
    one to trending status (we use the news as context for Gemini's rewrite)."""
    import xml.etree.ElementTree as ET
    import requests as _requests

    url = f"https://trends.google.com/trending/rss?geo={geo}"
    r = _requests.get(url, timeout=15, headers={
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/137.0 Safari/537.36"
        ),
    })
    r.raise_for_status()
    ns = {"ht": "https://trends.google.com/trending/rss"}
    root = ET.fromstring(r.text)
    items: list[dict] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        traffic = (item.findtext("ht:approx_traffic", namespaces=ns) or "").strip()
        news_titles: list[str] = []
        for n in item.findall("ht:news_item", ns):
            t = (n.findtext("ht:news_item_title", namespaces=ns) or "").strip()
            if t:
                news_titles.append(t)
        items.append({
            "title": title,
            "traffic": traffic,
            "news": news_titles[:3],
        })
    return items


@app.post("/api/trending")
def get_trending(req: TrendingRequest) -> dict:
    """Hybrid trending: pulls REAL Google Trends RSS for the geo, then asks
    Gemini to convert each trending query into an Object-Talk-suitable domain
    subject (skipping celebrity/news items that don't translate).
    Cached for 1 hour. `refresh=true` bypasses cache."""
    import requests as _requests
    sys.path.insert(0, str(ROOT))
    from config import GEMINI_API_KEY, GEMINI_TEXT_MODEL  # type: ignore

    cache_key = f"{req.geo}:{req.category}:{req.count}"
    now = time.time()
    if not req.refresh and cache_key in _TRENDING_CACHE:
        ts, items, raw = _TRENDING_CACHE[cache_key]
        if now - ts < _TRENDING_CACHE_TTL_S:
            return {
                "trending": items,
                "raw_trends": raw,
                "cached": True,
                "age_s": int(now - ts),
                "source": "Google Trends RSS (cached)",
            }

    # 1. Pull live Google Trends
    try:
        raw_trends = _fetch_google_trends_rss(req.geo)
    except Exception as e:
        raise HTTPException(502, f"Google Trends RSS unavailable: {e}")

    # 2. Build a digest the LLM can use
    top = raw_trends[:25]  # plenty of variety for Gemini to filter
    raw_summary_list = [
        {"query": t["title"], "traffic": t["traffic"], "news": t["news"]}
        for t in top
    ]
    trends_block = "\n".join(
        f"{i+1}. {t['title']} (traffic: {t['traffic'] or '?'})"
        + (f"\n   news: {' | '.join(t['news'])[:300]}" if t["news"] else "")
        for i, t in enumerate(top)
    )

    if req.category == "any":
        category_rules = (
            "- Be DIVERSE across categories (food, sports, fashion, festival, "
            "lifestyle, tech, etc). No two subjects in the same category."
        )
    else:
        category_rules = (
            f"- STRICT FILTER: every single one of the {req.count} subjects MUST belong "
            f"to the '{req.category}' category. NOT 'lifestyle' that's vaguely related — "
            f"truly {req.category}.\n"
            f"- If the raw trending list doesn't contain enough {req.category} items, "
            f"SUPPLEMENT with your own knowledge of what's actually trending RIGHT NOW "
            f"in {req.category} in {req.geo} — e.g. seasonal {req.category} items, "
            f"festival-tied {req.category}, recent consumer {req.category} crazes. "
            f"It's fine if only some come from the raw RSS list — quality of category "
            f"match matters more than RSS provenance.\n"
            f"- In the `category` field of each output item, you must literally write "
            f"'{req.category}' (no other value)."
        )

    prompt = (
        f"Below are the TOP {len(top)} REAL-TIME trending searches from Google Trends ({req.geo}) "
        f"right now, with the news headlines that pushed each to trending:\n\n"
        f"{trends_block}\n\n"
        f"From these REAL trending topics, produce {req.count} DOMAIN-based subjects suitable for "
        f"Object-Talk style Hindi reels (Pixar-style 3D characters personifying 5+ concrete objects).\n\n"
        f"RULES:\n"
        f"- SKIP individual person names / specific news events that don't translate to a domain "
        f"(e.g. skip 'Mouni Roy divorce', skip 'CAA passport ruling').\n"
        f"- TRANSFORM news-driven trends into the broader domain you can derive from them. "
        f"Example: 'Cannes 2026 [celebrity] outfit' → 'Cannes red carpet fashion' "
        f"(domain has gowns, jewelry, clutches, heels, makeup as 5 personifiable objects).\n"
        f"- KEEP product / category / sport / season / festival / food trends as-is if they already "
        f"name a domain (e.g. 'mango' → 'Indian mango varieties').\n"
        f"- Each final subject is 2-5 English words.\n"
        f"{category_rules}\n\n"
        f'Return strict JSON: {{"trending": [{{"subject": "...", "category": '
        f'"food|health|fitness|tech|lifestyle|home|vehicle|finance|festival|fashion|entertainment|sports", '
        f'"reason": "Based on trending: <original query OR seasonal context> — one-line context"}}, ...]}}'
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
            "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent?key={GEMINI_API_KEY}"
    r = _requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    try:
        parsed = json.loads(text)
    except Exception:
        raise HTTPException(502, "Gemini returned non-JSON for trending")
    items = parsed.get("trending", [])
    clean: list[dict] = []
    for it in items:
        s = (it.get("subject") or "").strip()
        if s and 2 <= len(s.split()) <= 6:
            clean.append({
                "subject": s,
                "category": it.get("category", "lifestyle"),
                "reason": (it.get("reason") or "")[:160],
            })
    _TRENDING_CACHE[cache_key] = (now, clean, raw_summary_list)
    return {
        "trending": clean,
        "raw_trends": raw_summary_list,
        "cached": False,
        "age_s": 0,
        "source": "Google Trends RSS (live)",
    }


class IdeaGenRequest(BaseModel):
    theme: str | None = None
    count: int = 10


@app.post("/api/ideas/generate")
def generate_ideas(req: IdeaGenRequest) -> dict:
    """Use Gemini to generate fresh subject ideas for Object Talk reels."""
    import requests as _requests
    sys.path.insert(0, str(ROOT))
    from config import GEMINI_API_KEY, GEMINI_TEXT_MODEL  # type: ignore
    theme_clause = f"Focus on the theme: {req.theme}." if req.theme else "Mix themes: food, fruits, vegetables, health, fitness, home appliances, vehicles, industry, tools, daily life."
    prompt = (
        f"Generate {req.count} short subject ideas for Object Talk style Hindi Reels. "
        f"Each subject is 2-5 English words (no Hindi), naming a domain whose objects can be personified "
        f"(e.g. 'electric vehicle charging', 'Indian street food', 'home gym equipment'). "
        f"{theme_clause} Avoid abstract domains. Avoid duplicates. "
        f'Return strict JSON: {{"ideas": ["...", "...", ...]}}'
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.95,
            "responseMimeType": "application/json",
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent?key={GEMINI_API_KEY}"
    r = _requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    return {"ideas": [str(x).strip() for x in parsed.get("ideas", []) if str(x).strip()]}


@app.get("/api/runs/{run_id}/scripts")
def get_scripts(run_id: str) -> dict:
    p = OUTPUT_DIR / run_id / "scripts.json"
    if not p.exists():
        raise HTTPException(404, "no scripts.json yet")
    return json.loads(p.read_text())


class ScriptsPayload(BaseModel):
    subject: str | None = None
    domain_phenomenon: str | None = None
    scripts: list[dict]


@app.put("/api/runs/{run_id}/scripts")
def put_scripts(run_id: str, body: ScriptsPayload) -> dict:
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    p = d / "scripts.json"
    existing = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except Exception:
            existing = {}
    merged = {
        "subject": body.subject or existing.get("subject") or run_id,
        "domain_phenomenon": body.domain_phenomenon or existing.get("domain_phenomenon", ""),
        "scripts": [],
    }
    for s in body.scripts:
        merged["scripts"].append({
            "object": s.get("object", ""),
            "image_prompt": s.get("image_prompt", ""),
            "hindi_script": s.get("hindi_script", ""),
            "word_count": len(s.get("hindi_script", "").split()),
        })
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    return merged


@app.get("/files/{run_id}/{filename}")
async def serve_artifact(run_id: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "bad filename")
    p = OUTPUT_DIR / run_id / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(p))


# Serve built frontend (if it exists). In dev, run `npm run dev` separately.
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="static")


def main() -> int:
    import uvicorn
    port = int(os.environ.get("PORT", "8765"))
    print(f"Object Talk webapp on http://localhost:{port}", flush=True)
    print(f"  Frontend dev: cd web && npm run dev  (proxies /api → :{port})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
