// API client + types for the Object Talk Pipeline backend.

export type RunStatus = 'idle' | 'running' | 'done' | 'error' | 'cancelled';
export type StepName = 'scripts' | 'images' | 'videos' | 'merge' | 'upload';

export type ErrorKind =
  | 'grok_quota'        // user's Grok quota exhausted
  | 'grok_rate_limit'   // server-side rate-limit toast
  | 'grok_overload'     // "Imagine is currently under heavy load"
  | 'grok_error'        // any other Grok refusal
  | null;

export interface GrokProfile {
  profile_path: string;
  state: 'missing' | 'anonymous' | 'authenticated';
  logged_in: boolean;
  cookies_count: number;
  host_count: number;
  session_cookies: string[];
  profile_age_s: number | null;
  cookies_age_s: number | null;
}

export interface RunSettings {
  video_provider: 'grok' | 'comfyui' | null;
  comfyui_engine: 'ltx' | 'wan' | 'wan_s2v' | null;
  skip_images: boolean;
  skip_upload: boolean;
  headless: boolean;
  parallel: boolean;
  privacy: 'public' | 'unlisted' | 'private';
  clip_count: number;
  clip_duration_s: number;
  max_words: number | null;
  manual_mode: boolean;
}

export interface LogLine {
  ts: number;             // unix timestamp (seconds) the bus saw the line
  text: string;
}

export type RunKind = 'object_talk' | 'product_video';

export interface Run {
  id: string;             // == slug == output dir name
  kind?: RunKind;
  subject: string;
  status: RunStatus;
  current_step: StepName | null;
  step_progress: { step: StepName; done: number; total: number } | null;
  created_at: number;     // unix ts
  updated_at: number;
  youtube_url: string | null;
  artifacts: Artifacts;
  is_active: boolean;     // a worker is currently running it
  log_tail: LogLine[];    // last N log lines with timestamps (for snapshot)
  error_kind?: ErrorKind;
  error_message?: string | null;
  // Legacy flat fields kept for back-compat with existing components.
  skip_images?: boolean;
  clip_count?: number;
  clip_duration_s?: number;
  // Full per-run settings panel — persisted in run_meta.json, surfaces here
  // so the run view can show + edit them.
  settings?: RunSettings;
}

export interface ProductVideoArtifacts {
  plan: string | null;
  briefs: string[];
  starters: string[];
  last_frames: string[];
  product_images: string[];
  scraped_text: string | null;
  approvals: {
    awaiting: number | null;
    approved: number[];
    rejected: number[];
  };
}

export interface Artifacts {
  scripts_json: string | null;       // /files/<id>/scripts.json or null
  images: string[];                  // /files/<id>/img_*
  videos: string[];                  // /files/<id>/vid_*
  merged: string | null;             // /files/<id>/merge.mp4
  metadata_json: string | null;
  product_video?: ProductVideoArtifacts;
}

export type VideoProvider = 'grok' | 'comfyui';
export type ComfyuiEngine = 'ltx' | 'wan' | 'wan_s2v';

export interface RunOptions {
  subject: string;
  privacy?: 'public' | 'unlisted' | 'private';
  headless?: boolean;
  skip_upload?: boolean;
  parallel?: boolean;
  from_step?: StepName;
  video_provider?: VideoProvider;
  comfyui_engine?: ComfyuiEngine;
  skip_images?: boolean;
  clip_count?: number;        // 1-20
  clip_duration_s?: number;   // 5-30
  max_words?: number | null;  // null → backend computes from duration
}

export type SseEventKind =
  | 'log' | 'step' | 'progress' | 'artifact' | 'status' | 'youtube' | 'error'
  | 'error_kind'
  | 'plan_ready' | 'clip_brief_ready' | 'clip_brief_refined'
  | 'starter_ready' | 'clip_video_ready'
  | 'last_frame_ready' | 'awaiting_approval' | 'approved';

export interface SseEvent {
  kind: SseEventKind;
  payload: any;
  ts?: number;            // backend now stamps every event; absent on legacy
}

const j = async <T>(r: Response): Promise<T> => {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
  return r.json();
};

export interface Script {
  object: string;
  image_prompt: string;
  hindi_script: string;
  action_script?: string;  // optional for back-compat with older runs
  word_count: number;
}

export interface ScriptsPayload {
  subject?: string;
  domain_phenomenon?: string;
  scripts: Script[];
}

export const api = {
  async list(): Promise<Run[]> {
    return j<Run[]>(await fetch('/api/runs'));
  },
  async get(id: string): Promise<Run> {
    return j<Run>(await fetch(`/api/runs/${id}`));
  },
  async getScripts(id: string): Promise<ScriptsPayload> {
    return j<ScriptsPayload>(await fetch(`/api/runs/${id}/scripts`));
  },
  async putScripts(id: string, payload: ScriptsPayload): Promise<ScriptsPayload> {
    return j<ScriptsPayload>(
      await fetch(`/api/runs/${id}/scripts`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
    );
  },
  async start(opts: RunOptions): Promise<Run> {
    return j<Run>(
      await fetch('/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(opts),
      }),
    );
  },
  async cancel(id: string): Promise<{ ok: true }> {
    return j(await fetch(`/api/runs/${id}/cancel`, { method: 'POST' }));
  },
  async retry(id: string, from_step: StepName, opts?: Partial<RunOptions>): Promise<Run> {
    return j<Run>(
      await fetch(`/api/runs/${id}/retry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_step, ...(opts || {}) }),
      }),
    );
  },
  async remove(id: string): Promise<{ ok: true }> {
    return j(await fetch(`/api/runs/${id}`, { method: 'DELETE' }));
  },
  async updateRunSettings(id: string, patch: Partial<RunSettings>): Promise<Run> {
    return j<Run>(
      await fetch(`/api/runs/${id}/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      }),
    );
  },
  openEvents(id: string, sinceCursor: number = 0): EventSource {
    // ?cursor= lets the server skip events the client has already seen so we
    // don't duplicate the log_tail every time the SSE reopens.
    const url = sinceCursor > 0
      ? `/api/runs/${id}/events?cursor=${sinceCursor}`
      : `/api/runs/${id}/events`;
    return new EventSource(url);
  },
  async regenScripts(id: string): Promise<Run> {
    return j<Run>(await fetch(`/api/runs/${id}/regen/scripts`, { method: 'POST' }));
  },
  async startManual(subject: string, skip_images: boolean = false,
                    comfyui_engine?: ComfyuiEngine,
                    clip_count: number = 5,
                    clip_duration_s: number = 10,
                    max_words?: number | null): Promise<Run> {
    return j<Run>(
      await fetch('/api/runs/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject, skip_images, comfyui_engine, clip_count, clip_duration_s, max_words }),
      }),
    );
  },
  async putComfyuiSettings(body: {
    url?: string;
    workflow?: string;
    video_provider?: VideoProvider;
    vbvr_lora?: string;
    vbvr_strength?: number;
    i2v_strength?: number;
  }): Promise<any> {
    return j(
      await fetch('/api/settings/comfyui', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    );
  },
  async regenImage(id: string, idx: number): Promise<Run> {
    return j<Run>(await fetch(`/api/runs/${id}/regen/image/${idx}`, { method: 'POST' }));
  },
  async regenScript(id: string, idx: number, hint?: string): Promise<Run> {
    return j<Run>(
      await fetch(`/api/runs/${id}/regen/script/${idx}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hint: hint || null }),
      }),
    );
  },
  async regenVideo(id: string, idx: number, video_provider?: VideoProvider,
                   comfyui_engine?: ComfyuiEngine): Promise<Run> {
    const body: any = {};
    if (video_provider) body.video_provider = video_provider;
    if (comfyui_engine) body.comfyui_engine = comfyui_engine;
    return j<Run>(await fetch(`/api/runs/${id}/regen/video/${idx}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
  },
  async manualMerge(id: string): Promise<Run> {
    return j<Run>(await fetch(`/api/runs/${id}/merge`, { method: 'POST' }));
  },
  async setYouTubeUrl(id: string, url: string | null): Promise<Run> {
    return j<Run>(
      await fetch(`/api/runs/${id}/youtube_url`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      }),
    );
  },
  async manualUpload(id: string, privacy: 'public' | 'unlisted' | 'private' = 'public'): Promise<Run> {
    return j<Run>(
      await fetch(`/api/runs/${id}/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ privacy }),
      }),
    );
  },
  async getSettings(): Promise<any> {
    return j(await fetch('/api/settings'));
  },
  async putGeminiSettings(body: {
    api_key?: string;
    text_model?: string;
    image_model?: string;
  }): Promise<any> {
    return j(
      await fetch('/api/settings/gemini', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    );
  },
  async listGeminiModels(): Promise<{ text_models: string[]; image_models: string[] }> {
    return j(await fetch('/api/settings/gemini/models'));
  },
  async uploadOauthClientSecret(jsonText: string): Promise<any> {
    return j(
      await fetch('/api/settings/youtube/client-secret', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: jsonText,
      }),
    );
  },
  async clearYouTubeToken(): Promise<any> {
    return j(await fetch('/api/settings/youtube/token', { method: 'DELETE' }));
  },
  async getGrokProfile(): Promise<GrokProfile> {
    return j<GrokProfile>(await fetch('/api/settings/grok/profile'));
  },
  async grokLogout(): Promise<GrokProfile> {
    return j<GrokProfile>(
      await fetch('/api/settings/grok/profile', { method: 'DELETE' }),
    );
  },
  async grokLogin(timeoutS: number = 600): Promise<{ ok: boolean; message: string }> {
    return j(
      await fetch('/api/settings/grok/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timeout_s: timeoutS }),
      }),
    );
  },
  async grokImportCookies(cookies: any[]): Promise<{
    ok: boolean; imported: number; ignored: number; status: GrokProfile;
  }> {
    return j(
      await fetch('/api/settings/grok/cookies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cookies }),
      }),
    );
  },
  async getTrending(geo = 'IN', category = 'any', refresh = false): Promise<{
    trending: { subject: string; category: string; reason: string }[];
    cached: boolean;
    age_s: number;
  }> {
    return j(
      await fetch('/api/trending', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ geo, category, refresh, count: 10 }),
      }),
    );
  },
  async generateIdeas(theme?: string, count = 10): Promise<{ ideas: string[] }> {
    return j(
      await fetch('/api/ideas/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: theme || null, count }),
      }),
    );
  },
};

export const STEP_ORDER: StepName[] = ['scripts', 'images', 'videos', 'merge', 'upload'];
export const STEP_LABEL: Record<StepName, string> = {
  scripts: 'Scripts',
  images: 'Images',
  videos: 'Videos',
  merge: 'Merge',
  upload: 'Upload',
};

// ── Product-video flow ──────────────────────────────────────────────────────
// Lives alongside the existing object-talk flow. Endpoints under /api/products
// and /api/runs/{id}/product-video and /api/runs/{id}/plan etc.

export interface ProductVideoOptions {
  review_mode: 'auto' | 'per_clip';
  clip_count: number;
  clip_duration_s: number;
  skip_upload?: boolean;
  privacy?: 'public' | 'unlisted' | 'private';
  parallel?: boolean;
  headless?: boolean;
}

export async function createProduct(
  form: FormData,
): Promise<{ run_id: string; product: any }> {
  return j<{ run_id: string; product: any }>(
    await fetch('/api/products', {
      method: 'POST',
      body: form,
    }),
  );
}

export async function startProductVideo(
  runId: string,
  opts: ProductVideoOptions,
): Promise<Run> {
  return j<Run>(
    await fetch(`/api/runs/${runId}/product-video`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    }),
  );
}

export async function getPlan(runId: string): Promise<any> {
  return j(await fetch(`/api/runs/${runId}/plan`));
}

export async function savePlan(runId: string, plan: any): Promise<any> {
  return j(
    await fetch(`/api/runs/${runId}/plan`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(plan),
    }),
  );
}

export async function getBrief(runId: string, idx: number): Promise<any> {
  return j(await fetch(`/api/runs/${runId}/brief/${idx}`));
}

export async function saveBrief(
  runId: string,
  idx: number,
  brief: any,
): Promise<any> {
  return j(
    await fetch(`/api/runs/${runId}/brief/${idx}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(brief),
    }),
  );
}

export async function approveClip(runId: string, idx: number): Promise<void> {
  const r = await fetch(`/api/runs/${runId}/approve/${idx}`, {
    method: 'POST',
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
}

export async function rejectClip(
  runId: string,
  idx: number,
  body: { reason?: string; edit_brief?: any },
): Promise<void> {
  const r = await fetch(`/api/runs/${runId}/reject/${idx}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
}
