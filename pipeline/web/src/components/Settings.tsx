import { useEffect, useState } from 'react';
import { api } from '../api';

interface SettingsData {
  gemini: {
    api_key_set: boolean;
    api_key_masked: string;
    text_model: string;
    image_model: string;
  };
  youtube: {
    client_secret_set: boolean;
    client_secret_path: string;
    token_set: boolean;
    token_path: string;
    token_age_s: number | null;
  };
  grok: {
    profile_set: boolean;
    profile_path: string;
    profile_age_s: number | null;
  };
}

function ago(s: number | null) {
  if (s === null) return 'never';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border font-medium ${
        ok
          ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/40'
          : 'bg-red-500/10 text-red-300 border-red-500/40'
      }`}
    >
      {ok ? '✓' : '✗'} {label}
    </span>
  );
}

export function Settings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingGemini, setSavingGemini] = useState(false);
  const [savedHint, setSavedHint] = useState<string | null>(null);

  // Form state
  const [apiKey, setApiKey] = useState('');
  const [textModel, setTextModel] = useState('');
  const [imageModel, setImageModel] = useState('');
  const [models, setModels] = useState<{ text_models: string[]; image_models: string[] }>({
    text_models: [],
    image_models: [],
  });

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await api.getSettings();
      setData(d);
      setTextModel(d.gemini.text_model);
      setImageModel(d.gemini.image_model);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const loadModels = async () => {
    try {
      const m = await api.listGeminiModels();
      setModels(m);
    } catch {/* needs key first; ignore */}
  };

  useEffect(() => {
    refresh().then(() => loadModels());
  }, []);

  const saveGemini = async () => {
    setSavingGemini(true);
    setError(null);
    try {
      const body: any = { text_model: textModel, image_model: imageModel };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const updated = await api.putGeminiSettings(body);
      setData(updated);
      setApiKey('');
      setSavedHint('Saved — restart backend to pick up new key in spawned subprocesses.');
      setTimeout(() => setSavedHint(null), 5000);
      loadModels();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSavingGemini(false);
    }
  };

  const onUploadOAuth = async (file: File) => {
    setError(null);
    try {
      const text = await file.text();
      // Validate JSON quickly client-side
      try { JSON.parse(text); } catch { throw new Error('File is not valid JSON'); }
      await api.uploadOauthClientSecret(text);
      await refresh();
      setSavedHint('OAuth client secret saved.');
      setTimeout(() => setSavedHint(null), 4000);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  const clearToken = async () => {
    if (!confirm('Forget cached YouTube OAuth token? Next upload will pop a browser for consent.')) return;
    await api.clearYouTubeToken();
    await refresh();
  };

  if (loading && !data) {
    return <div className="p-8 text-zinc-500 italic">Loading settings…</div>;
  }
  if (!data) {
    return <div className="p-8 text-red-400">{error || 'Failed to load settings'}</div>;
  }

  return (
    <div className="px-8 py-8 max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold mb-1">Settings</h1>
        <p className="text-sm text-zinc-500">
          Configure API keys and credentials here — no need to edit any files.
        </p>
      </div>

      {error && (
        <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded p-2">
          {error}
        </div>
      )}
      {savedHint && (
        <div className="text-sm text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 rounded p-2">
          {savedHint}
        </div>
      )}

      {/* Gemini */}
      <section className="rounded-lg border border-zinc-800 p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-base font-semibold flex items-center gap-2">
              Gemini API
              <StatusPill ok={data.gemini.api_key_set} label={data.gemini.api_key_set ? 'configured' : 'no key'} />
            </div>
            <div className="text-xs text-zinc-500 mt-1">
              Powers script generation, image generation, metadata generation, trending curation.
              {' '}
              <a
                href="https://aistudio.google.com/app/apikey"
                target="_blank"
                rel="noreferrer"
                className="text-emerald-400 hover:underline"
              >
                Get a free key at AI Studio →
              </a>
            </div>
          </div>
        </div>

        <div>
          <label className="block text-xs uppercase tracking-wider text-zinc-500 mb-1">
            API Key {data.gemini.api_key_set && <span className="text-zinc-600">— currently {data.gemini.api_key_masked}</span>}
          </label>
          <input
            type="password"
            placeholder={data.gemini.api_key_set ? 'Enter a new key to replace (leave blank to keep)' : 'AIzaSy…'}
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded text-sm font-mono
                       focus:outline-none focus:border-emerald-500"
            autoComplete="off"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs uppercase tracking-wider text-zinc-500 mb-1">Text model</label>
            <select
              value={textModel}
              onChange={e => setTextModel(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded text-sm
                         focus:outline-none focus:border-emerald-500"
            >
              {models.text_models.length === 0 && <option value={textModel}>{textModel}</option>}
              {models.text_models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-zinc-500 mb-1">Image model</label>
            <select
              value={imageModel}
              onChange={e => setImageModel(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded text-sm
                         focus:outline-none focus:border-emerald-500"
            >
              {models.image_models.length === 0 && <option value={imageModel}>{imageModel}</option>}
              {models.image_models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        </div>

        <button
          onClick={saveGemini}
          disabled={savingGemini}
          className="px-4 py-1.5 text-sm rounded-md bg-emerald-500 text-zinc-950 font-medium
                     hover:bg-emerald-400 disabled:opacity-50"
        >
          {savingGemini ? 'Saving…' : 'Save Gemini settings'}
        </button>
      </section>

      {/* YouTube OAuth */}
      <section className="rounded-lg border border-zinc-800 p-5 space-y-4">
        <div>
          <div className="text-base font-semibold flex items-center gap-2 flex-wrap">
            YouTube OAuth
            <StatusPill ok={data.youtube.client_secret_set} label={data.youtube.client_secret_set ? 'client_secret' : 'no client_secret'} />
            <StatusPill ok={data.youtube.token_set} label={data.youtube.token_set ? `token (${ago(data.youtube.token_age_s)})` : 'not authorized'} />
          </div>
          <div className="text-xs text-zinc-500 mt-1">
            Required to upload merged videos to YouTube.{' '}
            <a
              href="https://console.cloud.google.com/apis/credentials"
              target="_blank"
              rel="noreferrer"
              className="text-emerald-400 hover:underline"
            >
              Create a Desktop OAuth client →
            </a>{' '}
            then download the JSON and upload it below.
          </div>
        </div>

        <div className="text-xs text-zinc-500 space-y-1">
          <div>client_secret path: <span className="font-mono text-zinc-400">{data.youtube.client_secret_path}</span></div>
          <div>token path: <span className="font-mono text-zinc-400">{data.youtube.token_path}</span></div>
        </div>

        <div>
          <label
            className="inline-block px-4 py-1.5 text-sm rounded-md bg-zinc-800 border border-zinc-700
                       text-zinc-200 hover:bg-zinc-700 cursor-pointer"
          >
            {data.youtube.client_secret_set ? 'Replace client_secret.json' : 'Upload client_secret.json'}
            <input
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={e => {
                const f = e.target.files?.[0];
                if (f) onUploadOAuth(f);
                e.currentTarget.value = '';
              }}
            />
          </label>
          {data.youtube.token_set && (
            <button
              onClick={clearToken}
              className="ml-2 px-4 py-1.5 text-sm rounded-md bg-red-500/15 border border-red-500/40 text-red-300 hover:bg-red-500/25"
            >
              Forget token (re-auth on next upload)
            </button>
          )}
        </div>

        <div className="text-[11px] text-zinc-500 italic">
          The first upload after setting client_secret will pop a browser for Google consent
          (sign in as the channel owner). After that the token is cached and reused.
        </div>
      </section>

      {/* Grok */}
      <section className="rounded-lg border border-zinc-800 p-5 space-y-3">
        <div>
          <div className="text-base font-semibold flex items-center gap-2 flex-wrap">
            Grok browser session
            <StatusPill ok={data.grok.profile_set} label={data.grok.profile_set ? `logged in (${ago(data.grok.profile_age_s)})` : 'not set up'} />
          </div>
          <div className="text-xs text-zinc-500 mt-1">
            Grok video generation is browser-automated, so it needs your real Grok login cached
            in a persistent profile. Run the helper from a terminal:
          </div>
        </div>

        <pre className="bg-zinc-950 border border-zinc-800 rounded p-3 text-xs font-mono text-zinc-300 overflow-x-auto">
          cd {`${data.grok.profile_path.replace(/\/browser_data\/grok\/?$/, '')}`}
          {'\n'}
          python3.13 steps/grok_session.py
        </pre>

        <div className="text-xs text-zinc-500">
          profile path: <span className="font-mono text-zinc-400">{data.grok.profile_path}</span>
        </div>
      </section>
    </div>
  );
}
