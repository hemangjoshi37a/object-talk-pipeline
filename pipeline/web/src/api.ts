// API client + types for the Object Talk Pipeline backend.

export type RunStatus = 'idle' | 'running' | 'done' | 'error' | 'cancelled';
export type StepName = 'scripts' | 'images' | 'videos' | 'merge' | 'upload';

export type ErrorKind = 'grok_quota' | null;

export interface Run {
  id: string;             // == slug == output dir name
  subject: string;
  status: RunStatus;
  current_step: StepName | null;
  step_progress: { step: StepName; done: number; total: number } | null;
  created_at: number;     // unix ts
  updated_at: number;
  youtube_url: string | null;
  artifacts: Artifacts;
  is_active: boolean;     // a worker is currently running it
  log_tail: string[];     // last N log lines (for snapshot)
  error_kind?: ErrorKind;
  error_message?: string | null;
}

export interface Artifacts {
  scripts_json: string | null;       // /files/<id>/scripts.json or null
  images: string[];                  // /files/<id>/img_*
  videos: string[];                  // /files/<id>/vid_*
  merged: string | null;             // /files/<id>/merge.mp4
  metadata_json: string | null;
}

export interface RunOptions {
  subject: string;
  privacy?: 'public' | 'unlisted' | 'private';
  headless?: boolean;
  skip_upload?: boolean;
  parallel?: boolean;
  from_step?: StepName;
}

export interface SseEvent {
  kind: 'log' | 'step' | 'progress' | 'artifact' | 'status' | 'youtube' | 'error';
  payload: any;
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
  openEvents(id: string): EventSource {
    return new EventSource(`/api/runs/${id}/events`);
  },
  async regenScripts(id: string): Promise<Run> {
    return j<Run>(await fetch(`/api/runs/${id}/regen/scripts`, { method: 'POST' }));
  },
  async startManual(subject: string): Promise<Run> {
    return j<Run>(
      await fetch('/api/runs/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject }),
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
  async regenVideo(id: string, idx: number): Promise<Run> {
    return j<Run>(await fetch(`/api/runs/${id}/regen/video/${idx}`, { method: 'POST' }));
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
