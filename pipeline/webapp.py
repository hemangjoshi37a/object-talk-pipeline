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

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
WEB_DIST = ROOT / "web" / "dist"
PIPELINE_SCRIPT = ROOT / "pipeline.py"
PRODUCT_PIPELINE_SCRIPT = ROOT / "pipeline_product.py"
PYTHON = sys.executable or "python3.13"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STEP_ORDER = ["scripts", "images", "videos", "merge", "upload"]
STEP_FROM_HEADER = {  # parses lines like ">>> Step 1/5: generate scripts"
    1: "scripts", 2: "images", 3: "videos", 4: "merge", 5: "upload",
}
PRODUCT_STEP_FROM_HEADER = {  # parses lines like ">>> Step 4/7.2: starter image for clip 2"
    1: "scrape", 2: "plan", 3: "briefs",
    4: "starter", 5: "clip_video", 6: "last_frame", 7: "merge",
}

# ---------- Run bus (shared event stream per run_id) ----------

class RunBus:
    """Per-run-id shared state: event stream, log buffer, primary status.
    Multiple Jobs can publish into the same bus — pipeline run + concurrent
    image/video regens all share one stream so the UI sees ALL of their logs.
    """

    def __init__(self, run_id: str, loop: asyncio.AbstractEventLoop | None = None):
        self.run_id = run_id
        # `loop` may be None when the bus is created from a sync context just
        # to re-hydrate persisted events for a read-only API response. emit()
        # binds the loop lazily on first call, when we're guaranteed to be in
        # an async context (Job's background thread → bus.emit → run_coroutine).
        self.loop = loop
        self.events: list[dict] = []
        # Each log entry is {"ts": float, "text": str}. The frontend renders
        # ts beside the line so users can correlate events with wall-clock.
        self.log: list[dict] = []
        # Reset epoch — bumped whenever events get wiped (e.g. retry start).
        # In-flight SSE generators compare against this and break out when it
        # changes, forcing the client to reconnect and resync from cursor 0.
        self.reset_epoch: int = 0
        self.status = "idle"  # running | done | error | cancelled | idle
        self.current_step: str | None = None
        self.step_progress: dict | None = None
        self.youtube_url: str | None = None
        self.subject: str | None = None
        self.error_kind: str | None = None
        self.error_message: str | None = None
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.condition = asyncio.Condition()
        self._lock = threading.Lock()
        self.active_jobs: int = 0
        # Persisted event log: every event appended to output/<id>/events.jsonl
        # so logs survive backend restarts and pause/resume sessions. On bus
        # creation we replay the file into memory so SSE replay still works.
        self._events_path = OUTPUT_DIR / run_id / "events.jsonl"
        self._events_fh = None  # lazily opened on first emit
        self._load_persisted_events()

    def _load_persisted_events(self) -> None:
        """Re-hydrate `events` + `log` from disk so SSE replay works after restart."""
        if not self._events_path.exists():
            return
        try:
            with self._events_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    self.events.append(ev)
                    if ev.get("kind") == "log":
                        # Old events.jsonl lines may not have a ts — fall back
                        # to the file's mtime so the UI still gets a rough
                        # marker instead of "1970".
                        self.log.append({
                            "ts": ev.get("ts") or self._events_path.stat().st_mtime,
                            "text": ev.get("payload", ""),
                        })
            # Cap in-memory log buffer; the file keeps the full history.
            if len(self.log) > 2000:
                self.log = self.log[-2000:]
        except Exception:
            pass

    def emit(self, kind: str, payload: Any) -> None:
        # Every event carries the wall-clock timestamp the bus saw it. The
        # frontend uses this to render relative times beside each log line.
        event = {"kind": kind, "payload": payload, "ts": time.time()}
        with self._lock:
            self.events.append(event)
            self.updated_at = event["ts"]
            if kind == "log":
                # Store the log line as a [timestamp, text] tuple so the
                # snapshot endpoint can hand timing back to the UI even
                # without replaying the full event stream.
                self.log.append({"ts": event["ts"], "text": payload})
                if len(self.log) > 2000:
                    self.log = self.log[-2000:]
            try:
                self._events_path.parent.mkdir(parents=True, exist_ok=True)
                if self._events_fh is None:
                    self._events_fh = self._events_path.open("a", encoding="utf-8")
                self._events_fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                self._events_fh.flush()
            except Exception:
                pass
        # Lazily bind to the running asyncio loop on first emit — buses
        # created from a sync context (run_dict re-hydration) won't have one.
        if self.loop is None:
            try:
                self.loop = asyncio.get_event_loop()
            except RuntimeError:
                return  # no loop available (sync context); skip notification
        try:
            asyncio.run_coroutine_threadsafe(self._notify(), self.loop)
        except Exception:
            pass

    async def _notify(self) -> None:
        async with self.condition:
            self.condition.notify_all()


BUSES: dict[str, RunBus] = {}


def get_bus(run_id: str, subject: str | None = None) -> RunBus:
    bus = BUSES.get(run_id)
    if bus is None:
        # Try to grab the running loop; OK if absent — bus.emit() will rebind.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        bus = RunBus(run_id, loop)
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
                 label: str | None = None, subject: str | None = None,
                 extra_env: dict[str, str] | None = None):
        self.run_id = run_id
        self.cmd = cmd
        self.primary = primary
        self.label = label  # e.g. "regen-image-3", used to prefix log lines
        self.bus = get_bus(run_id, subject)
        # extra_env overrides .env per-spawn — used for per-run choices like
        # COMFYUI_ENGINE that shouldn't permanently mutate .env (which would
        # leak the choice into subsequent runs).
        self.extra_env = extra_env or {}
        self.proc: subprocess.Popen | None = None
        self.is_active = False

    @property
    def status(self) -> str:
        return self.bus.status if self.primary else ("running" if self.is_active else "done")

    @property
    def subject(self) -> str:
        return self.bus.subject or self.run_id

    def start(self) -> None:
        # Re-read .env at spawn time so Settings-page edits to API keys take
        # effect for subprocesses without requiring a backend restart. The
        # parent's os.environ may have stale values from when webapp.py first
        # loaded; an on-disk .env edit by the Settings UI wouldn't propagate.
        spawn_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                spawn_env[k.strip()] = v.strip()
        # Per-run overrides win over .env (e.g. user picked Wan for this run only).
        for k, v in self.extra_env.items():
            spawn_env[k] = v
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=spawn_env,
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
        m = re.search(r">>> Step (\d+)/7(?:\.(\d+))?:", line)
        if m:
            bus.current_step = PRODUCT_STEP_FROM_HEADER.get(int(m.group(1)))
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
            return
        # Grok failures — set a distinct error_kind so the UI can show a
        # clear, non-scary banner instead of a raw stack trace. The marker
        # `GROK_QUOTA_EXCEEDED` is used for ALL server-side refusals
        # (rate-limit, heavy-load, quota); we then sub-classify based on
        # the actual server message embedded in the line so the banner
        # copy can adapt.
        lower = line.lower()
        if "GROK_QUOTA_EXCEEDED" in line:
            if "heavy load" in lower:
                bus.error_kind = "grok_overload"
            elif "rate limit reached" in lower:
                bus.error_kind = "grok_rate_limit"
            elif "reached your limit" in lower or "quota" in lower:
                bus.error_kind = "grok_quota"
            else:
                bus.error_kind = "grok_error"
            bus.error_message = line.strip()
            bus.emit("error_kind", {"kind": bus.error_kind, "message": line.strip()})
            return
        # Product-video pipeline markers: emit dedicated SSE events so the new
        # UI timeline can highlight per-clip progress without parsing log text.
        if ">>> Plan ready" in line:
            bus.emit("plan_ready", None)
            return
        m = re.search(r">>> Brief ready:\s*(\d+)", line)
        if m:
            bus.emit("clip_brief_ready", {"clip": int(m.group(1))})
            return
        m = re.search(r">>> Brief refined:\s*(\d+)", line)
        if m:
            bus.emit("clip_brief_refined", {"clip": int(m.group(1))})
            return
        m = re.search(r">>> Starter ready:\s*(\d+)", line)
        if m:
            bus.emit("starter_ready", {"clip": int(m.group(1))})
            return
        m = re.search(r">>> Clip video ready:\s*(\d+)", line)
        if m:
            bus.emit("clip_video_ready", {"clip": int(m.group(1))})
            return
        m = re.search(r">>> Last frame ready:\s*(\d+)", line)
        if m:
            bus.emit("last_frame_ready", {"clip": int(m.group(1))})
            return
        m = re.search(r">>> Awaiting approval:\s*(\d+)", line)
        if m:
            bus.emit("awaiting_approval", {"clip": int(m.group(1))})
            return
        m = re.search(r">>> Approved:\s*(\d+)", line)
        if m:
            bus.emit("approved", {"clip": int(m.group(1))})
            return

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
            "product_video": _product_video_artifacts(run_id),
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
        "product_video": _product_video_artifacts(run_id),
    }


def _product_video_artifacts(run_id: str) -> dict:
    """Index product-video pipeline artifacts: plan, briefs, starters, last frames,
    user-uploaded product images, scraped page JSON, and approval state."""
    d = OUTPUT_DIR / run_id
    if not d.exists():
        return {
            "plan": None,
            "briefs": [],
            "starters": [],
            "last_frames": [],
            "product_images": [],
            "scraped_text": None,
            "approvals": {"awaiting": None, "approved": [], "rejected": []},
        }

    def fileurl(rel: str) -> str:
        return f"/files/{run_id}/{rel}"

    plan = fileurl("plan.json") if (d / "plan.json").exists() else None

    briefs_dir = d / "briefs"
    briefs: list[str] = []
    if briefs_dir.is_dir():
        briefs = sorted(
            fileurl(f"briefs/{p.name}")
            for p in briefs_dir.iterdir()
            if p.is_file() and p.name.startswith("brief_") and p.name.endswith(".json")
        )

    starters = sorted(fileurl(p.name) for p in d.glob("starter_*.png") if p.is_file())
    last_frames = sorted(fileurl(p.name) for p in d.glob("last_frame_*.png") if p.is_file())

    product_images: list[str] = []
    p_images_dir = d / "product" / "images"
    if p_images_dir.is_dir():
        product_images = sorted(
            fileurl(f"product/images/{p.name}")
            for p in p_images_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )

    scraped = d / "product" / "scraped" / "page.json"
    scraped_text = fileurl("product/scraped/page.json") if scraped.exists() else None

    approvals_dir = d / "approvals"
    approved: list[int] = []
    rejected: list[int] = []
    awaiting: int | None = None
    if approvals_dir.is_dir():
        for p in approvals_dir.iterdir():
            m = re.match(r"clip_(\d+)\.(approved|rejected)$", p.name)
            if not m:
                continue
            idx = int(m.group(1))
            if m.group(2) == "approved":
                approved.append(idx)
            else:
                rejected.append(idx)
        approved.sort()
        rejected.sort()
    bus = BUSES.get(run_id)
    if bus is not None:
        for ev in reversed(bus.events[-200:]):
            if ev.get("kind") == "awaiting_approval":
                cand = (ev.get("payload") or {}).get("clip")
                if isinstance(cand, int) and cand not in approved and cand not in rejected:
                    awaiting = cand
                break
    return {
        "plan": plan,
        "briefs": briefs,
        "starters": starters,
        "last_frames": last_frames,
        "product_images": product_images,
        "scraped_text": scraped_text,
        "approvals": {"awaiting": awaiting, "approved": approved, "rejected": rejected},
    }


def run_dict(run_id: str) -> dict:
    bus = BUSES.get(run_id)
    d = OUTPUT_DIR / run_id
    # If the run has a persisted event log but no live bus (e.g. backend just
    # restarted, or user opened a finished run after a fresh boot), spin up a
    # bus and re-hydrate from disk so the UI sees the full log timeline.
    if bus is None and (d / "events.jsonl").exists():
        try:
            bus = get_bus(run_id)
        except Exception:
            bus = None
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
    # error_kind/message survive even without a live bus by scanning the
    # persisted log tail for the GROK_QUOTA marker — so reopening the page
    # after a failed run still shows the clear banner instead of "error".
    error_kind = bus.error_kind if bus else None
    error_message = bus.error_message if bus else None
    if status == "error" and error_kind is None and bus:
        for entry in reversed(bus.log[-200:]):
            line = entry["text"] if isinstance(entry, dict) else entry
            if "GROK_QUOTA_EXCEEDED" in line:
                low = line.lower()
                if "heavy load" in low:
                    error_kind = "grok_overload"
                elif "rate limit reached" in low:
                    error_kind = "grok_rate_limit"
                elif "reached your limit" in low or "quota" in low:
                    error_kind = "grok_quota"
                else:
                    error_kind = "grok_error"
                error_message = line.strip()
                break
    # Per-run flags persisted to run_meta.json. Surfaced to the UI so the run
    # page shows what settings were used (and lets the user edit them via
    # PUT /api/runs/<id>/settings before hitting Retry).
    meta_file = d / "run_meta.json"
    meta: dict = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
        except Exception:
            meta = {}
    settings = {
        "video_provider": meta.get("video_provider") or None,
        "comfyui_engine": meta.get("comfyui_engine") or None,
        "skip_images": bool(meta.get("skip_images")),
        "skip_upload": bool(meta.get("skip_upload")),
        "headless": bool(meta.get("headless")),
        "parallel": bool(meta.get("parallel")),
        "privacy": meta.get("privacy") or "public",
        "clip_count": int(meta.get("clip_count") or 5),
        "clip_duration_s": int(meta.get("clip_duration_s") or 10),
        "max_words": meta.get("max_words"),
        "manual_mode": bool(meta.get("manual_mode")),
        # Surfaced for product_video runs so RunView can route per-clip approvals.
        "review_mode": meta.get("review_mode") if meta.get("review_mode") in ("auto", "per_clip") else None,
    }
    kind = meta.get("kind") if isinstance(meta.get("kind"), str) else "object_talk"
    if kind not in ("object_talk", "product_video"):
        kind = "object_talk"
    return {
        "id": run_id,
        "kind": kind,
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
        "error_kind": error_kind,
        "error_message": error_message,
        # legacy top-level fields kept for back-compat with existing UI code
        "skip_images": settings["skip_images"],
        "clip_count": settings["clip_count"],
        "clip_duration_s": settings["clip_duration_s"],
        # full settings object for the new UI panel
        "settings": settings,
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
    # Which backend renders the clips. Default falls through to env VIDEO_PROVIDER
    # (settable from the Settings page), which itself defaults to "grok".
    video_provider: str | None = None  # "grok" | "comfyui" | None (= use default)
    # Which ComfyUI engine — only meaningful when video_provider="comfyui".
    comfyui_engine: str | None = None  # "ltx" | "wan" | None
    # When true, skip Gemini image generation entirely and run the video provider
    # in text-only mode (character description folded into the prompt).
    skip_images: bool = False
    # Length controls — user-settable from the form. clip_count default 5, duration 10s.
    clip_count: int = 5            # 1..20
    clip_duration_s: int = 10      # 5..30
    # Manual override for max Hindi words per script. None → backend computes
    # from duration (duration_s * 3 - 5). When user enters a value in the form,
    # it overrides the calculated default and Gemini is told to land at ≤ N.
    max_words: int | None = None   # 4..120


class RetryOptions(BaseModel):
    from_step: str
    privacy: str | None = None
    headless: bool | None = None
    skip_upload: bool | None = None
    video_provider: str | None = None
    comfyui_engine: str | None = None
    skip_images: bool | None = None
    clip_count: int | None = None
    clip_duration_s: int | None = None
    max_words: int | None = None


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
    if opts.video_provider:
        cmd += ["--video-provider", opts.video_provider]
    if opts.skip_images:
        cmd.append("--skip-images")
    if opts.clip_count and opts.clip_count != 5:
        cmd += ["--clip-count", str(opts.clip_count)]
    if opts.clip_duration_s and opts.clip_duration_s != 10:
        cmd += ["--clip-duration-s", str(opts.clip_duration_s)]
    if opts.max_words:
        cmd += ["--max-words", str(opts.max_words)]
    if from_step:
        cmd += ["--from-step", from_step]
    return cmd


def _save_run_meta(run_id: str, **flags) -> None:
    """Persist per-run flags (like skip_images) so the UI can reflect them later."""
    d = OUTPUT_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    meta_file = d / "run_meta.json"
    existing: dict = {}
    if meta_file.exists():
        try:
            existing = json.loads(meta_file.read_text())
        except Exception:
            pass
    existing.update(flags)
    meta_file.write_text(json.dumps(existing, indent=2))


@app.post("/api/runs")
async def start_run(opts: RunOptions) -> dict:
    run_id = slug_of(opts.subject)
    if run_id in JOBS and JOBS[run_id].is_active:
        raise HTTPException(409, f"already running: {run_id}")
    _save_run_meta(run_id,
                   skip_images=opts.skip_images,
                   video_provider=opts.video_provider or "",
                   comfyui_engine=opts.comfyui_engine or "",
                   clip_count=opts.clip_count,
                   clip_duration_s=opts.clip_duration_s,
                   max_words=opts.max_words,
                   privacy=opts.privacy,
                   headless=opts.headless,
                   skip_upload=opts.skip_upload,
                   parallel=opts.parallel)
    extra_env: dict[str, str] = {}
    if opts.comfyui_engine in ("ltx", "wan", "wan_s2v"):
        extra_env["COMFYUI_ENGINE"] = opts.comfyui_engine
        extra_env["COMFYUI_WORKFLOW"] = ""  # force engine default workflow
    job = Job(run_id, _build_cmd(opts), primary=True, subject=opts.subject,
              extra_env=extra_env)
    JOBS[run_id] = job
    job.start()
    return run_dict(run_id)


class ManualRunRequest(BaseModel):
    subject: str
    skip_images: bool = False  # hide the images section in the run view + skip step 2
    comfyui_engine: str | None = None  # "ltx" | "wan" — sticky for this run's regen buttons
    clip_count: int = 5
    clip_duration_s: int = 10
    max_words: int | None = None  # manual override; None → backend default


@app.post("/api/runs/manual")
async def start_manual_run(req: ManualRunRequest) -> dict:
    """Create a manual run: only generates scripts.json. User triggers everything else
    via the per-item Generate buttons + manual Merge + manual Upload."""
    run_id = slug_of(req.subject)
    if run_id in JOBS and JOBS[run_id].status == "running":
        raise HTTPException(409, f"already running: {run_id}")
    d = OUTPUT_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    _save_run_meta(run_id,
                   skip_images=req.skip_images,
                   video_provider="",  # manual mode = user picks per-clip
                   comfyui_engine=req.comfyui_engine or "",
                   clip_count=req.clip_count,
                   clip_duration_s=req.clip_duration_s,
                   max_words=req.max_words,
                   privacy="public",
                   headless=False,
                   skip_upload=False,
                   parallel=False,
                   manual_mode=True)
    cmd = [PYTHON, "-u", str(ROOT / "steps" / "generate_scripts.py"),
           req.subject, "--out", str(d / "scripts.json"),
           "--count", str(req.clip_count),
           "--duration-s", str(req.clip_duration_s)]
    if req.max_words:
        cmd += ["--max-words", str(req.max_words)]
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
    # ComfyUI may still be processing the prompt the pipeline submitted —
    # killing the Python script doesn't interrupt the GPU job. Tell ComfyUI
    # to stop the current run and clear its queue so the next retry starts clean.
    env = _read_env_file()
    comfy_url = env.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    import urllib.request as _ur
    for path, body in [("/interrupt", b""), ("/queue", b'{"clear":true}')]:
        try:
            req = _ur.Request(f"{comfy_url}{path}", data=body,
                              headers={"Content-Type": "application/json"})
            _ur.urlopen(req, timeout=3).read()
        except Exception:
            pass  # best-effort; ComfyUI may be on a different host or unreachable
    return {"ok": True}


@app.post("/api/runs/{run_id}/retry")
async def retry_run(run_id: str, opts: RetryOptions) -> dict:
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    existing = JOBS.get(run_id)
    if existing and existing.is_active:
        raise HTTPException(409, "still running")

    # Reset the in-memory + on-disk event log so each retry shows a fresh
    # slate in the UI. Without this, the events.jsonl accumulates every
    # prior attempt's setup banner + traceback, and the UI replays all of
    # it on connect — the user perceives this as "logs keep duplicating".
    events_path = d / "events.jsonl"
    if events_path.exists():
        events_path.unlink(missing_ok=True)
    bus = BUSES.get(run_id)
    if bus is not None:
        with bus._lock:
            bus.events.clear()
            bus.log.clear()
            if bus._events_fh is not None:
                try: bus._events_fh.close()
                except Exception: pass
                bus._events_fh = None
            bus.error_kind = None
            bus.error_message = None
            bus.reset_epoch += 1
        # Wake any in-flight SSE generators so they re-check the epoch
        # and break out — the client auto-reconnects from cursor 0.
        if bus.loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(bus._notify(), bus.loop)
            except Exception:
                pass

    # When retrying from an earlier step, wipe artifacts of LATER steps so the
    # rebuild starts truly fresh (otherwise pipeline.py sees existing img_*/vid_*
    # and "skips" them, defeating the retry intent).
    step_order = ["scripts", "images", "videos", "merge", "upload"]
    try:
        from_idx = step_order.index(opts.from_step)
    except ValueError:
        from_idx = 0
    # Retry semantics: RESUME-friendly. Wipe only artifacts that are
    # logically invalidated by re-running the chosen step, NOT artifacts
    # produced by that step itself. The pipeline's "skip if exists" logic
    # picks up where it left off (e.g. retry-from-videos with 2 vid_*.mp4
    # already on disk → renders only the missing 3, not all 5).
    DOWNSTREAM = ("merge.mp4", "metadata.json", "youtube_url.txt")
    if from_idx <= step_order.index("scripts"):
        # Scripts change ⇒ images and videos are stale, must regenerate.
        for p in list(d.glob("img_*")) + list(d.glob("vid_*.mp4")):
            p.unlink(missing_ok=True)
        for name in DOWNSTREAM:
            (d / name).unlink(missing_ok=True)
    elif from_idx == step_order.index("images"):
        # Images change ⇒ videos referencing them are stale.
        for p in d.glob("vid_*.mp4"):
            p.unlink(missing_ok=True)
        for name in DOWNSTREAM:
            (d / name).unlink(missing_ok=True)
    elif from_idx == step_order.index("videos"):
        # Keep already-rendered vid_*.mp4 so the videos step resumes from
        # the first missing one. Only wipe downstream artifacts.
        for name in DOWNSTREAM:
            (d / name).unlink(missing_ok=True)
    elif from_idx == step_order.index("merge"):
        for name in DOWNSTREAM:
            (d / name).unlink(missing_ok=True)
    elif from_idx == step_order.index("upload"):
        for name in ("metadata.json", "youtube_url.txt"):
            (d / name).unlink(missing_ok=True)

    scripts_path = d / "scripts.json"
    subject = run_id
    if scripts_path.exists():
        try:
            subject = json.loads(scripts_path.read_text()).get("subject", run_id)
        except Exception:
            pass
    # Read the original run's saved settings from run_meta.json — these become
    # the defaults for every setting NOT explicitly overridden in the retry
    # request. Without this, retry silently falls back to .env defaults and the
    # user's run silently switches provider/engine.
    meta: dict = {}
    meta_file = d / "run_meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
        except Exception:
            meta = {}

    def _pick(req_val, meta_key, default):
        """Override > meta > default."""
        if req_val is not None:
            return req_val
        v = meta.get(meta_key)
        return default if v is None or v == "" else v

    privacy = _pick(opts.privacy, "privacy", "public")
    headless = _pick(opts.headless, "headless", False)
    skip_upload = _pick(opts.skip_upload, "skip_upload", False)
    skip_images = _pick(opts.skip_images, "skip_images", False)
    video_provider = _pick(opts.video_provider, "video_provider", None) or None
    comfyui_engine = _pick(opts.comfyui_engine, "comfyui_engine", None) or None
    clip_count = int(_pick(opts.clip_count, "clip_count", 5))
    clip_duration_s = int(_pick(opts.clip_duration_s, "clip_duration_s", 10))
    max_words = opts.max_words if opts.max_words is not None else meta.get("max_words")
    run_opts = RunOptions(subject=subject, privacy=privacy, headless=headless, skip_upload=skip_upload,
                          video_provider=video_provider, skip_images=skip_images,
                          comfyui_engine=comfyui_engine,
                          clip_count=clip_count, clip_duration_s=clip_duration_s,
                          max_words=max_words)
    # Persist the resolved settings back to meta so subsequent retries see them
    # (e.g. user overrides provider on a retry → that becomes the new default).
    _save_run_meta(run_id,
                   skip_images=skip_images,
                   video_provider=video_provider or "",
                   comfyui_engine=comfyui_engine or "",
                   clip_count=clip_count,
                   clip_duration_s=clip_duration_s,
                   max_words=max_words,
                   privacy=privacy,
                   headless=headless,
                   skip_upload=skip_upload)
    # Build extra_env so the per-run engine choice wins over .env (same as start_run).
    extra_env: dict[str, str] = {}
    if comfyui_engine in ("ltx", "wan", "wan_s2v"):
        extra_env["COMFYUI_ENGINE"] = comfyui_engine
        extra_env["COMFYUI_WORKFLOW"] = ""  # force engine default workflow
    job = Job(run_id, _build_cmd(run_opts, from_step=opts.from_step), primary=True, subject=subject,
              extra_env=extra_env)
    JOBS[run_id] = job
    job.start()
    return run_dict(run_id)


class SettingsPatch(BaseModel):
    """Partial settings update for a run — every field optional. Only changes
    `run_meta.json`; does NOT trigger a re-run. Hit Retry afterwards if you
    want the new settings to take effect."""
    privacy: str | None = None
    headless: bool | None = None
    skip_upload: bool | None = None
    parallel: bool | None = None
    video_provider: str | None = None
    comfyui_engine: str | None = None
    skip_images: bool | None = None
    clip_count: int | None = None
    clip_duration_s: int | None = None
    max_words: int | None = None


@app.put("/api/runs/{run_id}/settings")
async def update_run_settings(run_id: str, patch: SettingsPatch) -> dict:
    """Edit per-run settings after the fact. The next Retry will pick them up."""
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    updates: dict = {}
    for field in ("privacy", "headless", "skip_upload", "parallel",
                  "video_provider", "comfyui_engine", "skip_images",
                  "clip_count", "clip_duration_s", "max_words"):
        v = getattr(patch, field, None)
        if v is not None:
            # Normalize empty strings to "" (stored) so retry treats them as unset.
            updates[field] = v
    if updates:
        _save_run_meta(run_id, **updates)
    return run_dict(run_id)


def _spawn_step_job(run_id: str, subject: str, cmd: list[str],
                    step: str | None = None,
                    extra_env: dict[str, str] | None = None) -> Job:
    """Start a primary subprocess (manual scripts gen, video regen, merge, upload).
    Only one primary job per run_id at a time (Chrome lock + status conflicts)."""
    if run_id in JOBS and JOBS[run_id].is_active:
        raise HTTPException(409, "another job already running for this run")
    job = Job(run_id, cmd, primary=True, subject=subject, extra_env=extra_env)
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


class RegenScriptOpts(BaseModel):
    hint: str | None = None


@app.post("/api/runs/{run_id}/regen/script/{idx}")
async def regen_one_script(run_id: str, idx: int, opts: RegenScriptOpts | None = None) -> dict:
    """Regenerate a single script slot (1..5) keeping the other 4 intact.
    Aux job — concurrent with image/video regens (no Chrome lock involved)."""
    if idx < 1 or idx > 5:
        raise HTTPException(400, "idx must be 1..5")
    d = OUTPUT_DIR / run_id
    if not (d / "scripts.json").exists():
        raise HTTPException(404, "no scripts.json — generate scripts first")
    subject = _subject_for(run_id)
    cmd = [PYTHON, "-u", str(ROOT / "steps" / "generate_scripts.py"),
           subject, "--out", str(d / "scripts.json"),
           "--only", str(idx)]
    if opts and opts.hint:
        cmd.extend(["--hint", opts.hint])
    _spawn_aux_job(run_id, cmd, label=f"regen-script-{idx}")
    return run_dict(run_id)


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


class RegenVideoOpts(BaseModel):
    video_provider: str | None = None  # "grok" | "comfyui" | None (= use env default)
    comfyui_engine: str | None = None  # "ltx" | "wan" — only relevant when provider=comfyui


@app.post("/api/runs/{run_id}/regen/video/{idx}")
async def regen_video(run_id: str, idx: int, opts: RegenVideoOpts | None = None) -> dict:
    if idx < 1 or idx > 5:
        raise HTTPException(400, "idx must be 1..5")
    d = OUTPUT_DIR / run_id
    if not (d / "scripts.json").exists():
        raise HTTPException(404, "no scripts.json")
    # Read the run's saved settings so the per-run choices stick — request body
    # overrides everything, then run_meta, then env default. Without this, hitting
    # "Generate" on a clip silently fell back to .env's VIDEO_PROVIDER (often
    # `comfyui`) and ignored the user's per-run Grok selection.
    meta_file = d / "run_meta.json"
    meta: dict = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
        except Exception:
            pass
    run_skip_images = bool(meta.get("skip_images"))
    provider = ((opts.video_provider if opts else None)
                or (meta.get("video_provider") or None)
                or os.environ.get("VIDEO_PROVIDER", "grok"))
    # Grok needs the per-script image as the first frame (unless skip_images);
    # ComfyUI is fine without one.
    if provider == "grok" and not run_skip_images and not list(d.glob(f"img_{idx:02d}_*")):
        raise HTTPException(400, f"no image for index {idx} — generate the image first")
    # Delete the existing video so the generator doesn't skip it
    for existing in d.glob(f"vid_{idx:02d}_*.mp4"):
        existing.unlink()
    step_script = "generate_videos_comfyui.py" if provider == "comfyui" else "generate_videos.py"
    cmd = [PYTHON, "-u", str(ROOT / "steps" / step_script),
           str(d), "--only", str(idx)]
    # Build per-spawn env overrides (skip_images + engine choice)
    extra_env: dict[str, str] = {}
    if run_skip_images:
        extra_env["SKIP_IMAGES"] = "1"
    engine = ((opts.comfyui_engine if opts else None)
              or (meta.get("comfyui_engine") or None)
              or os.environ.get("COMFYUI_ENGINE", "ltx"))
    if engine in ("ltx", "wan", "wan_s2v"):
        extra_env["COMFYUI_ENGINE"] = engine
        extra_env["COMFYUI_WORKFLOW"] = ""  # force engine default workflow
    _spawn_step_job(run_id, _subject_for(run_id), cmd, step="videos",
                    extra_env=extra_env)
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


class YouTubeUrlBody(BaseModel):
    url: str | None = None  # null/empty clears the URL


_YT_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})(?:[/?&].*)?$"
)


@app.put("/api/runs/{run_id}/youtube_url")
async def set_youtube_url(run_id: str, body: YouTubeUrlBody) -> dict:
    """Manually set or clear the YouTube URL for a run.

    Useful when the upload step succeeded on YouTube but the pipeline failed to
    capture the URL (e.g. the upload subprocess died after the API call but
    before writing youtube_url.txt). Pass null or "" to clear.
    """
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    yt_file = d / "youtube_url.txt"
    url = (body.url or "").strip()
    if url:
        if not _YT_URL_RE.match(url):
            raise HTTPException(400, "URL must look like https://youtube.com/shorts/<id> or https://youtu.be/<id>")
        yt_file.write_text(url + "\n")
    else:
        if yt_file.exists():
            yt_file.unlink()
    # Push to live bus if there is one so the SSE stream picks it up
    bus = BUSES.get(run_id)
    if bus is not None:
        bus.youtube_url = url or None
        bus.emit("youtube", url or "")
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
    bus = BUSES.pop(run_id, None)
    if bus and bus._events_fh:
        try:
            bus._events_fh.close()
        except Exception:
            pass
    d = OUTPUT_DIR / run_id
    if d.exists():
        shutil.rmtree(d)
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request):
    bus = BUSES.get(run_id)
    # Resume cursor: client can pass ?cursor=N to skip events it's already seen.
    # Falls back to SSE Last-Event-ID header (auto-set by browser on reconnect).
    try:
        cursor_param = int(request.query_params.get("cursor", "") or
                           request.headers.get("last-event-id", "") or "0")
    except (TypeError, ValueError):
        cursor_param = 0

    async def gen():
        cursor = cursor_param
        if bus:
            # Capture the bus's reset epoch at connect time. If retry_run
            # wipes the bus mid-stream, the epoch bumps and this generator
            # breaks out, forcing the client to reconnect from cursor 0.
            my_epoch = bus.reset_epoch
            # If the client's resume cursor (from Last-Event-ID) is past the
            # current buffer length — usually because the bus was wiped on
            # retry — rewind to 0 so we send the fresh events from the start.
            if cursor > len(bus.events):
                cursor = 0
            # Replay everything in the bus so far
            while cursor < len(bus.events):
                yield f"id: {cursor}\ndata: {json.dumps(bus.events[cursor])}\n\n"
                cursor += 1
            # Then stream new events; stay open as long as any job is feeding
            while True:
                if await request.is_disconnected():
                    break
                # Bus was reset (retry started). Close so the client
                # reconnects with a fresh cursor.
                if bus.reset_epoch != my_epoch:
                    break
                if cursor < len(bus.events):
                    while cursor < len(bus.events):
                        yield f"id: {cursor}\ndata: {json.dumps(bus.events[cursor])}\n\n"
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


def _grok_profile_status(profile_dir: Path) -> dict:
    """Inspect the Chromium profile dir Grok uses. Reads cookie names directly
    from the cookies sqlite DB (no browser launch needed) to classify the
    profile as:
      - "missing"        — directory empty / no Cookies DB
      - "anonymous"      — Cookies DB present but only anon cookies
                           (x-anonuserid, cf_clearance, etc.) — Grok will let
                           you submit prompts but the share URLs 403 on download
      - "authenticated"  — at least one session/auth cookie present
    """
    import sqlite3
    status = {
        "profile_path": str(profile_dir),
        "state": "missing",
        "logged_in": False,
        "cookies_count": 0,
        "host_count": 0,
        "session_cookies": [],
        "profile_age_s": None,
        "cookies_age_s": None,
    }
    if not profile_dir.exists():
        return status
    status["profile_age_s"] = int(time.time() - profile_dir.stat().st_mtime)
    cookies_db = profile_dir / "Default" / "Cookies"
    if not cookies_db.exists() or cookies_db.stat().st_size < 100:
        return status
    status["cookies_age_s"] = int(time.time() - cookies_db.stat().st_mtime)
    # Open RO so we don't conflict with a live Chromium process.
    try:
        conn = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True, timeout=2)
        rows = list(conn.execute(
            "SELECT host_key, name FROM cookies "
            "WHERE host_key LIKE '%grok%' OR host_key LIKE '%x.ai%' OR host_key LIKE '%x.com%'"
        ))
        conn.close()
    except Exception:
        return status
    status["cookies_count"] = len(rows)
    status["host_count"] = len({h for h, _ in rows})
    # Real Grok/X session cookies — exact names, not substrings (substring
    # matching gave false positives on __stripe_sid, x-anonuserid, etc).
    AUTH_NAMES = {
        "sso", "auth_token", "auth-token", "next-auth.session-token",
        "__Secure-next-auth.session-token", "_grok_session",
        "twid", "ct0", "kdt",  # X.com login cookies (X uses the same SSO)
        "x-user-id",
    }
    # Names we KNOW are not auth, so don't get confused if they appear.
    NON_AUTH_PREFIXES = ("__stripe", "__cf", "mp_", "OptanonConsent",
                          "i18nextLng", "grok_device_id", "x-anonuserid",
                          "x-challenge", "x-signature")
    session_names = []
    has_anon = False
    for _, name in rows:
        if name.startswith(NON_AUTH_PREFIXES):
            if name == "x-anonuserid":
                has_anon = True
            continue
        if name in AUTH_NAMES:
            session_names.append(name)
    status["session_cookies"] = session_names
    if session_names:
        status["state"] = "authenticated"
        status["logged_in"] = True
    elif has_anon or rows:
        status["state"] = "anonymous"
    return status


def _comfyui_health(url: str, timeout: float = 2.5) -> dict:
    """Best-effort ping of ComfyUI at the configured URL — used by Settings UI."""
    import urllib.request as _ur
    try:
        with _ur.urlopen(f"{url.rstrip('/')}/system_stats", timeout=timeout) as r:
            data = json.loads(r.read())
        return {
            "reachable": True,
            "version": data.get("system", {}).get("comfyui_version"),
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)[:140]}


def _comfyui_list_workflows(url: str, timeout: float = 2.5) -> list[str]:
    """Pull the saved-workflow list from ComfyUI userdata."""
    import urllib.request as _ur
    try:
        with _ur.urlopen(f"{url.rstrip('/')}/userdata?dir=workflows", timeout=timeout) as r:
            data = json.loads(r.read())
        # Strip .json suffix so the dropdown shows the slug used in the dropdown
        return sorted(f[:-5] if f.endswith(".json") else f for f in data)
    except Exception:
        return []


def _comfyui_list_loras(url: str, timeout: float = 3.0) -> list[str]:
    """Pull the list of LoRA filenames the ComfyUI host can see (LoraLoaderModelOnly)."""
    import urllib.request as _ur
    try:
        with _ur.urlopen(f"{url.rstrip('/')}/object_info/LoraLoaderModelOnly", timeout=timeout) as r:
            data = json.loads(r.read())
        files = data.get("LoraLoaderModelOnly", {}).get("input", {}).get("required", {}).get("lora_name", [])
        # files[0] is the list of names when it's a combo input
        return sorted(files[0]) if files and isinstance(files[0], list) else []
    except Exception:
        return []


@app.get("/api/settings")
def get_settings() -> dict:
    env = _read_env_file()
    gem = env.get("GEMINI_API_KEY", "")
    youtube_token = DEFAULT_YOUTUBE_TOKEN
    client_secret = DEFAULT_CLIENT_SECRET
    grok_profile = Path(env.get("GROK_PROFILE_DIR", str(ROOT / "browser_data" / "grok")))
    comfy_url = env.get("COMFYUI_URL", "http://127.0.0.1:8188")
    comfy_workflow = env.get("COMFYUI_WORKFLOW", "ltx23_nerdy_rodent")
    comfy_health = _comfyui_health(comfy_url)
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
        "grok": _grok_profile_status(grok_profile),
        "comfyui": {
            "url": comfy_url,
            "workflow": comfy_workflow,
            "reachable": comfy_health.get("reachable", False),
            "version": comfy_health.get("version"),
            "health_error": comfy_health.get("error"),
            "available_workflows": _comfyui_list_workflows(comfy_url) if comfy_health.get("reachable") else [],
            "available_loras": _comfyui_list_loras(comfy_url) if comfy_health.get("reachable") else [],
            "vbvr_lora": env.get("COMFYUI_VBVR_LORA", "VBVR-official-comfyui.safetensors"),
            "vbvr_strength": float(env.get("COMFYUI_VBVR_STRENGTH", "0.7")),
            "i2v_strength": float(env.get("COMFYUI_I2V_STRENGTH", "0.7")),
            "engine": env.get("COMFYUI_ENGINE", "ltx"),
        },
        "video_provider": env.get("VIDEO_PROVIDER", "grok"),
    }


class ComfyUISettingsBody(BaseModel):
    url: str | None = None
    workflow: str | None = None
    video_provider: str | None = None  # "grok" | "comfyui" — the default provider for new runs
    engine: str | None = None          # "ltx" | "wan" — default ComfyUI engine
    vbvr_lora: str | None = None       # filename of the VBVR LoRA (or "" to disable)
    vbvr_strength: float | None = None # 0.0 to 2.0 (LoRA strength typical range)
    i2v_strength: float | None = None  # 0.0 to 1.0


@app.get("/api/settings/comfyui/workflow")
def download_comfyui_workflow() -> Response:
    """Stream the configured workflow JSON from ComfyUI's userdata so users can
    download it from the Settings UI (and re-upload into another ComfyUI host).
    """
    env = _read_env_file()
    url = env.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    wf = env.get("COMFYUI_WORKFLOW", "ltx23_nerdy_rodent")
    import urllib.request as _ur, urllib.parse as _up
    try:
        with _ur.urlopen(f"{url}/userdata/workflows%2F{_up.quote(wf + '.json', safe='')}",
                         timeout=10) as r:
            body = r.read()
    except Exception as e:
        raise HTTPException(502, f"Could not fetch workflow from ComfyUI ({url}): {e}")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{wf}.json"'},
    )


@app.put("/api/settings/comfyui")
def put_comfyui_settings(body: ComfyUISettingsBody) -> dict:
    env = _read_env_file()
    if body.url is not None and body.url.strip():
        env["COMFYUI_URL"] = body.url.strip().rstrip("/")
        os.environ["COMFYUI_URL"] = env["COMFYUI_URL"]
    if body.workflow is not None and body.workflow.strip():
        env["COMFYUI_WORKFLOW"] = body.workflow.strip()
        os.environ["COMFYUI_WORKFLOW"] = env["COMFYUI_WORKFLOW"]
    if body.video_provider is not None:
        if body.video_provider not in ("grok", "comfyui"):
            raise HTTPException(400, "video_provider must be 'grok' or 'comfyui'")
        env["VIDEO_PROVIDER"] = body.video_provider
        os.environ["VIDEO_PROVIDER"] = body.video_provider
    if body.engine is not None:
        if body.engine not in ("ltx", "wan"):
            raise HTTPException(400, "engine must be 'ltx' or 'wan'")
        env["COMFYUI_ENGINE"] = body.engine
        os.environ["COMFYUI_ENGINE"] = body.engine
    if body.vbvr_lora is not None:
        env["COMFYUI_VBVR_LORA"] = body.vbvr_lora.strip()
        os.environ["COMFYUI_VBVR_LORA"] = env["COMFYUI_VBVR_LORA"]
    if body.vbvr_strength is not None:
        if not 0.0 <= body.vbvr_strength <= 2.0:
            raise HTTPException(400, "vbvr_strength must be between 0.0 and 2.0")
        env["COMFYUI_VBVR_STRENGTH"] = str(body.vbvr_strength)
        os.environ["COMFYUI_VBVR_STRENGTH"] = env["COMFYUI_VBVR_STRENGTH"]
    if body.i2v_strength is not None:
        if not 0.0 <= body.i2v_strength <= 1.0:
            raise HTTPException(400, "i2v_strength must be between 0.0 and 1.0")
        env["COMFYUI_I2V_STRENGTH"] = str(body.i2v_strength)
        os.environ["COMFYUI_I2V_STRENGTH"] = env["COMFYUI_I2V_STRENGTH"]
    _write_env_file(env)
    return get_settings()


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


def _grok_profile_dir() -> Path:
    env = _read_env_file()
    return Path(env.get("GROK_PROFILE_DIR", str(ROOT / "browser_data" / "grok")))


@app.get("/api/settings/grok/profile")
def get_grok_profile() -> dict:
    """Inspect-only — same data Settings shows, useful for polling after login."""
    return _grok_profile_status(_grok_profile_dir())


@app.delete("/api/settings/grok/profile")
def grok_logout() -> dict:
    """Wipe the Grok browser profile (logout). The next launch will be anonymous,
    forcing the user through login again."""
    p = _grok_profile_dir()
    if p.exists():
        # Refuse if another process has the profile locked — Chromium leaves
        # SingletonLock when running and clobbering it would corrupt state.
        try:
            shutil.rmtree(p)
        except OSError as e:
            raise HTTPException(409, f"can't remove profile (browser still running?): {e}")
    return _grok_profile_status(p)


class GrokCookiesPaste(BaseModel):
    """Cookie blob exported from a logged-in browser. Accepts any of the common
    extension export formats — Cookie-Editor (chrome), EditThisCookie (chrome),
    or a raw Playwright/Puppeteer storage_state. Each cookie object should at
    minimum have `name`, `value`, `domain`."""
    cookies: list[dict]


class GrokLoginRequest(BaseModel):
    timeout_s: int = 600  # how long to keep the headed browser open


@app.post("/api/settings/grok/login")
async def grok_login(req: GrokLoginRequest) -> dict:
    """Spawn a headed Chromium pointing at the Grok profile + grok.com so the
    user can log in. The browser stays open until they close it OR the timeout
    fires. We can't pop a window over SSH — if there's no DISPLAY/WAYLAND
    available, return instructions instead of failing silently.
    """
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if not display:
        raise HTTPException(400,
            "No DISPLAY/WAYLAND_DISPLAY in the backend's environment. The "
            "Grok login flow needs a real X server (or ssh -X). Options:\n"
            "  1. Re-start the backend from a desktop terminal: "
            "DISPLAY=:0 ./run.sh\n"
            "  2. SSH in with X-forwarding: ssh -X user@host then ./run.sh\n"
            "  3. Use a separate machine to log in, then copy "
            "browser_data/grok/ to this host."
        )
    p = _grok_profile_dir()
    p.mkdir(parents=True, exist_ok=True)
    # Spawn the cloakbrowser context in a background thread; the page stays
    # open until the user closes it OR timeout fires. We don't await — return
    # immediately so the UI can show "browser launching…".
    import threading
    def _runner() -> None:
        try:
            import cloakbrowser
            ctx = cloakbrowser.launch_persistent_context(
                user_data_dir=str(p),
                headless=False,
                viewport={"width": 1280, "height": 800},
                args=["--window-size=1280,800"],
            )
            try:
                page = ctx.new_page()
                page.goto("https://grok.com/", wait_until="domcontentloaded", timeout=30000)
                # Stay open — user does the login, we wait for them to close the tab.
                deadline = time.time() + req.timeout_s
                while time.time() < deadline:
                    if not ctx.pages:
                        break
                    time.sleep(2)
            finally:
                try: ctx.close()
                except Exception: pass
        except Exception as e:
            print(f"[grok-login] failed: {e}", flush=True)
    threading.Thread(target=_runner, daemon=True).start()
    return {"ok": True, "message": "Browser launching — complete the Grok login, then close the window."}


def _normalize_cookie(c: dict) -> dict | None:
    """Coerce one cookie object from any common export format into Playwright's
    add_cookies() shape. Returns None on malformed input.

    Supported sources (field aliases handled here):
      - Cookie-Editor (Chrome ext)          → {expirationDate, hostOnly, sameSite: "no_restriction"}
      - EditThisCookie (Chrome ext)          → similar, sameSite numeric
      - Playwright/Puppeteer storage_state  → already in target format
      - Curl / DevTools "Copy as cURL"      → typically only name+value+domain
    """
    name = c.get("name")
    value = c.get("value")
    domain = c.get("domain") or c.get("host")
    if not name or value is None or not domain:
        return None
    out: dict = {
        "name": str(name),
        "value": str(value),
        "domain": str(domain),
        "path": c.get("path") or "/",
    }
    # Expiry: prefer numeric "expires" / "expirationDate"; ignore "session" cookies.
    exp = c.get("expires") or c.get("expirationDate")
    if isinstance(exp, (int, float)) and exp > 0:
        out["expires"] = float(exp)
    # Booleans
    if c.get("httpOnly") is not None: out["httpOnly"] = bool(c["httpOnly"])
    if c.get("secure") is not None:   out["secure"]   = bool(c["secure"])
    # SameSite normalization — Playwright accepts "Strict"/"Lax"/"None"
    ss = c.get("sameSite")
    if isinstance(ss, str):
        s = ss.lower().replace("_", "")
        if s in ("strict",):              out["sameSite"] = "Strict"
        elif s in ("lax",):               out["sameSite"] = "Lax"
        elif s in ("none", "norestriction", "unspecified"): out["sameSite"] = "None"
    elif isinstance(ss, (int, float)):
        # EditThisCookie maps 0=None, 1=Lax, 2=Strict
        out["sameSite"] = {0: "None", 1: "Lax", 2: "Strict"}.get(int(ss), "Lax")
    return out


@app.post("/api/settings/grok/cookies")
def grok_import_cookies(req: GrokCookiesPaste) -> dict:
    """Import a cookies blob exported from a logged-in browser elsewhere. Runs
    headless Playwright so it works WITHOUT a display. After import, the
    profile shows as `authenticated` if the blob contained Grok/X session
    cookies.

    How users get the blob (any one works):
      - Cookie-Editor extension → "Export" → "Export as JSON" while on grok.com
      - EditThisCookie → "Export"
      - DevTools Application → Cookies → right-click → Copy all (paste as JSON)
    """
    cookies = [_normalize_cookie(c) for c in req.cookies]
    cookies = [c for c in cookies if c]
    if not cookies:
        raise HTTPException(400, "No valid cookies in payload (need name/value/domain)")
    # Only keep cookies for Grok / X domains — guards against accidentally
    # pasting a dump from another site.
    relevant = [c for c in cookies
                if any(d in c["domain"] for d in ("grok.com", "x.ai", "x.com"))]
    if not relevant:
        raise HTTPException(400,
            f"None of the {len(cookies)} cookies are for grok.com / x.ai / x.com — "
            "did you export from the wrong tab?")
    p = _grok_profile_dir()
    p.mkdir(parents=True, exist_ok=True)
    try:
        import cloakbrowser
        ctx = cloakbrowser.launch_persistent_context(
            user_data_dir=str(p), headless=True,
            viewport={"width": 1280, "height": 720},
        )
        try:
            ctx.add_cookies(relevant)
        finally:
            ctx.close()
    except Exception as e:
        raise HTTPException(500, f"Failed to write cookies into profile: {e}")
    status = _grok_profile_status(p)
    return {
        "ok": True,
        "imported": len(relevant),
        "ignored": len(cookies) - len(relevant),
        "status": status,
    }


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


# ---------- Product-video flow ----------

_PRODUCT_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_PRODUCT_VID_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_PRODUCT_IMG_MIME = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/gif": ".gif", "image/bmp": ".bmp",
}
_PRODUCT_VID_MIME = {
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "video/x-matroska": ".mkv",
}


def _safe_upload_ext(upload: UploadFile, allowed: set[str],
                     mime_map: dict[str, str], default: str) -> str:
    """Pick a safe file extension for an UploadFile based on filename + content-type."""
    name = (upload.filename or "").lower()
    ext = Path(name).suffix
    if ext in allowed:
        return ".jpg" if ext == ".jpeg" else ext
    ct = (upload.content_type or "").lower()
    mapped = mime_map.get(ct)
    if mapped:
        return mapped
    return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


@app.post("/api/products")
async def create_product(
    company_name: str = Form(...),
    product_name: str = Form(...),
    product_description: str | None = Form(None),
    target_audience: str | None = Form(None),
    tone: str | None = Form(None),
    feeling_to_evoke: str | None = Form(None),
    vision_statement: str | None = Form(None),
    visual_style_preference: str | None = Form(None),
    language: str = Form("hi"),
    total_duration_s: int = Form(50),
    clip_duration_s: int = Form(10),
    voice_tone: str | None = Form(None),
    voice_type: str | None = Form(None),
    structure_hook_prompt: str | None = Form(None),
    structure_middle_prompt: str | None = Form(None),
    structure_cta_prompt: str | None = Form(None),
    website_url: str | None = Form(None),
    product_images: list[UploadFile] = File(default=[]),
    product_videos: list[UploadFile] = File(default=[]),
) -> dict:
    """Create a product brief + save uploaded media. Returns the new run_id
    (slug of product_name) and the persisted brief dict. Does NOT start the
    pipeline — call POST /api/runs/{run_id}/product-video for that."""
    if not product_name.strip():
        raise HTTPException(400, "product_name is required")
    run_id = slug_of(product_name)
    if not run_id:
        raise HTTPException(400, "product_name produced an empty slug")

    product_dir = OUTPUT_DIR / run_id / "product"
    images_dir = product_dir / "images"
    videos_dir = product_dir / "videos"
    images_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)

    saved_image_paths: list[str] = []
    saved_video_paths: list[str] = []

    imgs = (product_images or [])[:10]
    for i, up in enumerate(imgs, start=1):
        if up is None or not up.filename:
            continue
        ext = _safe_upload_ext(up, _PRODUCT_IMG_EXTS, _PRODUCT_IMG_MIME, ".png")
        dest = images_dir / f"img_{i:02d}{ext}"
        with dest.open("wb") as f:
            while True:
                chunk = await up.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        await up.close()
        saved_image_paths.append(f"product/images/{dest.name}")

    vids = (product_videos or [])[:3]
    for i, up in enumerate(vids, start=1):
        if up is None or not up.filename:
            continue
        ext = _safe_upload_ext(up, _PRODUCT_VID_EXTS, _PRODUCT_VID_MIME, ".mp4")
        dest = videos_dir / f"vid_{i:02d}{ext}"
        with dest.open("wb") as f:
            while True:
                chunk = await up.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        await up.close()
        saved_video_paths.append(f"product/videos/{dest.name}")

    brief = {
        "company_name": company_name,
        "product_name": product_name,
        "product_description": product_description or "",
        "target_audience": target_audience or "",
        "tone": tone or "",
        "feeling_to_evoke": (feeling_to_evoke or "").strip(),
        "vision_statement": (vision_statement or "").strip(),
        "visual_style_preference": (visual_style_preference or "").strip(),
        "language": language,
        "total_duration_s": int(total_duration_s),
        "clip_duration_s": int(clip_duration_s),
        "voice_tone": voice_tone or "",
        "voice_type": voice_type or "",
        "structure_hook_prompt": structure_hook_prompt or "",
        "structure_middle_prompt": structure_middle_prompt or "",
        "structure_cta_prompt": structure_cta_prompt or "",
        "website_url": website_url or "",
        "product_images": saved_image_paths,
        "product_videos": saved_video_paths,
    }
    _atomic_write_json(product_dir / "brief.json", brief)

    _save_run_meta(
        run_id,
        kind="product_video",
        product_name=product_name,
        company_name=company_name,
        language=language,
        total_duration_s=int(total_duration_s),
        clip_duration_s=int(clip_duration_s),
        website_url=website_url or "",
    )
    return {"run_id": run_id, "product": brief}


class ProductVideoRunOptions(BaseModel):
    review_mode: str = "auto"            # "auto" | "per_clip"
    clip_count: int = 5
    clip_duration_s: int = 10
    skip_upload: bool = False
    privacy: str = "public"
    parallel: bool = False
    headless: bool = False


@app.post("/api/runs/{run_id}/product-video")
async def start_product_video(run_id: str, opts: ProductVideoRunOptions) -> dict:
    d = OUTPUT_DIR / run_id
    if not d.exists() or not (d / "product" / "brief.json").exists():
        raise HTTPException(404, "no product brief for this run — POST /api/products first")
    if opts.review_mode not in ("auto", "per_clip"):
        raise HTTPException(400, "review_mode must be 'auto' or 'per_clip'")
    if run_id in JOBS and JOBS[run_id].is_active:
        raise HTTPException(409, f"already running: {run_id}")

    _save_run_meta(
        run_id,
        kind="product_video",
        review_mode=opts.review_mode,
        clip_count=opts.clip_count,
        clip_duration_s=opts.clip_duration_s,
        skip_upload=opts.skip_upload,
        privacy=opts.privacy,
        parallel=opts.parallel,
        headless=opts.headless,
    )

    cmd = [
        PYTHON, "-u", str(PRODUCT_PIPELINE_SCRIPT), run_id,
        "--review-mode", opts.review_mode,
        "--clip-count", str(opts.clip_count),
        "--clip-duration-s", str(opts.clip_duration_s),
        "--privacy", opts.privacy,
    ]
    if opts.skip_upload:
        cmd.append("--skip-upload")
    if opts.parallel:
        cmd.append("--parallel")
    if opts.headless:
        cmd.append("--headless")

    try:
        brief = json.loads((d / "product" / "brief.json").read_text())
        subject = brief.get("product_name") or run_id
    except Exception:
        subject = run_id

    job = Job(run_id, cmd, primary=True, subject=subject,
              extra_env={"PRODUCT_RUN_ID": run_id})
    JOBS[run_id] = job
    job.start()
    return run_dict(run_id)


@app.get("/api/runs/{run_id}/plan")
def get_plan(run_id: str) -> dict:
    p = OUTPUT_DIR / run_id / "plan.json"
    if not p.exists():
        raise HTTPException(404, "no plan.json yet")
    try:
        return json.loads(p.read_text())
    except Exception as e:
        raise HTTPException(500, f"plan.json is not valid JSON: {e}")


@app.put("/api/runs/{run_id}/plan")
async def put_plan(run_id: str, request: Request) -> dict:
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "plan must be a JSON object")
    _atomic_write_json(d / "plan.json", data)
    return data


@app.get("/api/runs/{run_id}/brief/{idx}")
def get_brief(run_id: str, idx: int) -> dict:
    if idx < 1:
        raise HTTPException(400, "idx must be >= 1")
    p = OUTPUT_DIR / run_id / "briefs" / f"brief_{idx:02d}.json"
    if not p.exists():
        raise HTTPException(404, f"no brief_{idx:02d}.json")
    try:
        return json.loads(p.read_text())
    except Exception as e:
        raise HTTPException(500, f"brief_{idx:02d}.json is not valid JSON: {e}")


@app.put("/api/runs/{run_id}/brief/{idx}")
async def put_brief(run_id: str, idx: int, request: Request) -> dict:
    if idx < 1:
        raise HTTPException(400, "idx must be >= 1")
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "brief must be a JSON object")
    _atomic_write_json(d / "briefs" / f"brief_{idx:02d}.json", data)
    return data


class ClipRejectBody(BaseModel):
    reason: str | None = None
    edit_brief: dict | None = None


@app.post("/api/runs/{run_id}/approve/{idx}")
async def approve_clip(run_id: str, idx: int) -> dict:
    if idx < 1:
        raise HTTPException(400, "idx must be >= 1")
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    approvals = d / "approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    # Clear any prior rejection sentinel so a re-approval supersedes it.
    (approvals / f"clip_{idx:02d}.rejected").unlink(missing_ok=True)
    (approvals / f"clip_{idx:02d}.approved").touch()
    bus = BUSES.get(run_id)
    if bus is not None:
        bus.emit("approved", {"clip": idx})
    return {"ok": True}


@app.post("/api/runs/{run_id}/reject/{idx}")
async def reject_clip(run_id: str, idx: int, body: ClipRejectBody) -> dict:
    if idx < 1:
        raise HTTPException(400, "idx must be >= 1")
    d = OUTPUT_DIR / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    if body.edit_brief is not None:
        if not isinstance(body.edit_brief, dict):
            raise HTTPException(400, "edit_brief must be a JSON object")
        _atomic_write_json(d / "briefs" / f"brief_{idx:02d}.json", body.edit_brief)
    # Wipe artifacts for this clip so the pipeline regenerates them after pickup.
    (d / f"starter_{idx:02d}.png").unlink(missing_ok=True)
    (d / f"last_frame_{idx:02d}.png").unlink(missing_ok=True)
    for v in d.glob(f"vid_{idx:02d}_*.mp4"):
        v.unlink(missing_ok=True)
    approvals = d / "approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    (approvals / f"clip_{idx:02d}.approved").unlink(missing_ok=True)
    (approvals / f"clip_{idx:02d}.rejected").write_text(body.reason or "")
    return {"ok": True}


@app.get("/files/{run_id}/{filename:path}")
async def serve_artifact(run_id: str, filename: str):
    # Allow nested paths (e.g. product/images/img_01.png, briefs/brief_01.json)
    # but reject anything that could escape the run dir or expose dotfiles.
    if not filename or filename.startswith("/") or ".." in filename.split("/"):
        raise HTTPException(400, "bad filename")
    for seg in filename.split("/"):
        if not seg or seg.startswith("."):
            raise HTTPException(400, "bad filename")
    run_root = (OUTPUT_DIR / run_id).resolve()
    try:
        p = (OUTPUT_DIR / run_id / filename).resolve()
    except Exception:
        raise HTTPException(400, "bad filename")
    try:
        p.relative_to(run_root)
    except ValueError:
        raise HTTPException(400, "bad filename")
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(p))


# Serve built frontend (if it exists). In dev, run `npm run dev` separately.
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="static")


def main() -> int:
    import uvicorn
    port = int(os.environ.get("PORT", "8765"))
    # Auto-reload on Python file changes by default (no need to restart the
    # backend after every code edit). Disable by exporting OBJTALK_NO_RELOAD=1
    # for clean production runs.
    auto_reload = os.environ.get("OBJTALK_NO_RELOAD") != "1"
    reload_dirs: list[str] | None = None
    if auto_reload:
        reload_dirs = [str(ROOT), str(ROOT / "steps")]
    print(f"Object Talk webapp on http://localhost:{port}"
          + (" (auto-reload ON)" if auto_reload else ""), flush=True)
    print(f"  Frontend dev: cd web && npm run dev  (proxies /api → :{port})", flush=True)
    if auto_reload:
        # uvicorn.run with reload=True needs an import string, not the app object,
        # so it can re-import the module on each restart.
        uvicorn.run(
            "webapp:app",
            host="127.0.0.1",
            port=port,
            log_level="info",
            reload=True,
            reload_dirs=reload_dirs,
            reload_excludes=["output/*", "browser_data/*", "web/node_modules/*", "web/dist/*", "*.log"],
        )
    else:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
