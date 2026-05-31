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
    profile_path: string;
    state: 'missing' | 'anonymous' | 'authenticated';
    logged_in: boolean;
    cookies_count: number;
    host_count: number;
    session_cookies: string[];
    profile_age_s: number | null;
    cookies_age_s: number | null;
  };
  comfyui: {
    url: string;
    workflow: string;
    reachable: boolean;
    version: string | null;
    health_error: string | null;
    available_workflows: string[];
    available_loras: string[];
    vbvr_lora: string;
    vbvr_strength: number;
    i2v_strength: number;
  };
  video_provider: 'grok' | 'comfyui';
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
  // ComfyUI form state
  const [comfyUrl, setComfyUrl] = useState('');
  const [comfyWorkflow, setComfyWorkflow] = useState('');
  const [videoProvider, setVideoProvider] = useState<'grok' | 'comfyui'>('grok');
  const [vbvrLora, setVbvrLora] = useState('');
  const [vbvrStrength, setVbvrStrength] = useState(0.7);
  const [i2vStrength, setI2vStrength] = useState(0.7);
  const [savingComfy, setSavingComfy] = useState(false);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await api.getSettings();
      setData(d);
      setTextModel(d.gemini.text_model);
      setImageModel(d.gemini.image_model);
      setComfyUrl(d.comfyui?.url || 'http://127.0.0.1:8188');
      setComfyWorkflow(d.comfyui?.workflow || 'ltx23_nerdy_rodent');
      setVideoProvider(d.video_provider || 'grok');
      setVbvrLora(d.comfyui?.vbvr_lora || '');
      setVbvrStrength(d.comfyui?.vbvr_strength ?? 0.7);
      setI2vStrength(d.comfyui?.i2v_strength ?? 0.7);
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

  const saveComfy = async () => {
    setSavingComfy(true);
    setError(null);
    try {
      const updated = await api.putComfyuiSettings({
        url: comfyUrl,
        workflow: comfyWorkflow,
        video_provider: videoProvider,
        vbvr_lora: vbvrLora,
        vbvr_strength: vbvrStrength,
        i2v_strength: i2vStrength,
      });
      setData(updated);
      setSavedHint('ComfyUI settings saved.');
      setTimeout(() => setSavedHint(null), 4000);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSavingComfy(false);
    }
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

      {/* ComfyUI */}
      <section className="rounded-lg border border-zinc-800 p-5 space-y-4">
        <div>
          <div className="text-base font-semibold flex items-center gap-2 flex-wrap">
            ComfyUI (LTX-2.3 video provider)
            <StatusPill
              ok={!!data.comfyui?.reachable}
              label={data.comfyui?.reachable
                ? `reachable${data.comfyui.version ? ` (v${data.comfyui.version})` : ''}`
                : 'unreachable'}
            />
          </div>
          <div className="text-xs text-zinc-500 mt-1">
            Local/self-hosted alternative to Grok. Renders clips on your GPU via a saved
            ComfyUI workflow. The URL can point to any host on the network — videos are
            fetched over HTTP from <code className="text-zinc-400">/view</code>, not the
            local filesystem.
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="block text-xs uppercase tracking-wider text-zinc-500 mb-1">
              Default provider
            </label>
            <select
              value={videoProvider}
              onChange={e => setVideoProvider(e.target.value as 'grok' | 'comfyui')}
              className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded text-sm
                         focus:outline-none focus:border-emerald-500"
            >
              <option value="grok">Grok Imagine (cloud)</option>
              <option value="comfyui">ComfyUI (local)</option>
            </select>
            <div className="text-[10px] text-zinc-600 mt-1">
              Pre-selected on Auto/Manual pages
            </div>
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-zinc-500 mb-1">
              Server URL
            </label>
            <input
              value={comfyUrl}
              onChange={e => setComfyUrl(e.target.value)}
              placeholder="http://127.0.0.1:8188"
              className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded text-sm font-mono
                         focus:outline-none focus:border-emerald-500"
            />
            <div className="text-[10px] text-zinc-600 mt-1">
              Any host:port reachable from this backend
            </div>
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-zinc-500 mb-1">
              Workflow
            </label>
            {data.comfyui?.available_workflows?.length ? (
              <select
                value={comfyWorkflow}
                onChange={e => setComfyWorkflow(e.target.value)}
                className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded text-sm
                           focus:outline-none focus:border-emerald-500"
              >
                {!data.comfyui.available_workflows.includes(comfyWorkflow) && (
                  <option value={comfyWorkflow}>{comfyWorkflow}</option>
                )}
                {data.comfyui.available_workflows.map(w =>
                  <option key={w} value={w}>{w}</option>
                )}
              </select>
            ) : (
              <input
                value={comfyWorkflow}
                onChange={e => setComfyWorkflow(e.target.value)}
                placeholder="ltx23_nerdy_rodent"
                className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded text-sm font-mono
                           focus:outline-none focus:border-emerald-500"
              />
            )}
            <div className="text-[10px] text-zinc-600 mt-1">
              Saved in ComfyUI userdata
            </div>
          </div>
        </div>

        {!data.comfyui?.reachable && data.comfyui?.health_error && (
          <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2">
            Health check failed: <code>{data.comfyui.health_error}</code>
          </div>
        )}

        {/* Per-generation tuning: I2V strength + extra LoRA */}
        <div className="rounded border border-zinc-800 bg-zinc-900/30 p-3 space-y-3">
          <div className="text-[10px] uppercase tracking-wider text-zinc-500">
            Per-generation tuning
          </div>

          <div className="grid grid-cols-[1fr_120px] gap-3 items-center">
            <div>
              <label className="block text-xs text-zinc-300 mb-0.5">
                Image-to-video strength
              </label>
              <div className="text-[10px] text-zinc-500">
                How tightly the Gemini image pins the first frame. 1.0 = rigid (can cause
                "great frame 1-2 then video degrades"); 0.5–0.8 = looser, better motion.
              </div>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={0} max={1} step={0.05}
                value={i2vStrength}
                onChange={e => setI2vStrength(parseFloat(e.target.value))}
                className="flex-1 accent-emerald-500"
              />
              <span className="text-xs font-mono text-zinc-300 w-8 text-right">{i2vStrength.toFixed(2)}</span>
            </div>
          </div>

          <div className="grid grid-cols-[1fr_120px] gap-3 items-center">
            <div>
              <label className="block text-xs text-zinc-300 mb-0.5">
                VBVR LoRA <span className="text-zinc-500 font-normal">(Video Reasoning)</span>
              </label>
              <div className="text-[10px] text-zinc-500">
                Improves prompt following, temporal consistency, character behavior.
                Chained between UNet and both samplers. Empty = disabled.
              </div>
              {data.comfyui?.available_loras?.length > 0 ? (
                <select
                  value={vbvrLora}
                  onChange={e => setVbvrLora(e.target.value)}
                  className="mt-1 w-full px-2 py-1 bg-zinc-900 border border-zinc-700 rounded text-xs
                             focus:outline-none focus:border-emerald-500"
                >
                  <option value="">— disabled —</option>
                  {!data.comfyui.available_loras.includes(vbvrLora) && vbvrLora && (
                    <option value={vbvrLora}>{vbvrLora} (missing)</option>
                  )}
                  {data.comfyui.available_loras.map(l => (
                    <option key={l} value={l}>{l}</option>
                  ))}
                </select>
              ) : (
                <input
                  value={vbvrLora}
                  onChange={e => setVbvrLora(e.target.value)}
                  placeholder="VBVR-official-comfyui.safetensors"
                  className="mt-1 w-full px-2 py-1 bg-zinc-900 border border-zinc-700 rounded text-xs font-mono"
                />
              )}
            </div>
            <div className="flex items-center gap-2 self-end">
              <input
                type="range"
                min={0} max={1.5} step={0.05}
                value={vbvrStrength}
                onChange={e => setVbvrStrength(parseFloat(e.target.value))}
                disabled={!vbvrLora}
                className="flex-1 accent-emerald-500 disabled:opacity-40"
              />
              <span className="text-xs font-mono text-zinc-300 w-8 text-right">{vbvrStrength.toFixed(2)}</span>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={saveComfy}
            disabled={savingComfy}
            className="px-4 py-1.5 text-sm rounded-md bg-emerald-500 text-zinc-950 font-medium
                       hover:bg-emerald-400 disabled:opacity-50"
          >
            {savingComfy ? 'Saving…' : 'Save ComfyUI settings'}
          </button>
          {data.comfyui?.reachable && (
            <a
              href="/api/settings/comfyui/workflow"
              download
              className="px-4 py-1.5 text-sm rounded-md bg-zinc-800 border border-zinc-700
                         text-zinc-200 hover:bg-zinc-700"
            >
              ⬇ Download workflow JSON
            </a>
          )}
          <span className="text-[11px] text-zinc-500 ml-1">
            Re-import in another ComfyUI: drag the JSON onto the canvas.
          </span>
        </div>

        <div className="text-[11px] text-zinc-500 leading-relaxed border-l-2 border-zinc-700 pl-2">
          <b className="text-zinc-400">Image-to-video:</b> when a clip's image
          {' '}(<code>img_NN_*.png</code>) exists, it's uploaded to ComfyUI and locked as the
          first frame via <code>LTXVImgToVideo</code>. Set <code>COMFYUI_I2V=0</code> in
          {' '}<code>.env</code> to force pure text-to-video.
        </div>
      </section>

      {/* Grok */}
      <GrokProfileCard
        grok={data.grok}
        onRefresh={refresh}
      />
    </div>
  );
}

function GrokProfileCard({
  grok, onRefresh,
}: {
  grok: SettingsData['grok'];
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [cookiesText, setCookiesText] = useState('');

  const stateStyle = {
    missing:       { color: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',     label: 'not set up' },
    anonymous:     { color: 'bg-amber-500/15 text-amber-300 border-amber-500/40',  label: 'anonymous (not logged in)' },
    authenticated: { color: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40', label: 'logged in' },
  }[grok.state];

  const logout = async () => {
    if (!confirm(
      'Wipe the Grok browser profile? All cached cookies + login state will be lost.\n\n' +
      'You will need to log in again before the next Grok video run.',
    )) return;
    setBusy('logout');
    setErr(null);
    setMsg(null);
    try {
      await api.grokLogout();
      setMsg('Profile wiped.');
      await onRefresh();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  };

  const login = async () => {
    setBusy('login');
    setErr(null);
    setMsg(null);
    try {
      const r = await api.grokLogin();
      setMsg(r.message);
      // Poll the profile state every few seconds while the user logs in
      const start = Date.now();
      const tick = async () => {
        if (Date.now() - start > 8 * 60_000) return;
        await onRefresh();
        if (Date.now() - start > 8 * 60_000) return;
        setTimeout(tick, 4000);
      };
      setTimeout(tick, 4000);
    } catch (e: any) {
      const errMsg = e?.message || String(e);
      setErr(errMsg);
      // If the error indicates no display, auto-open the paste fallback —
      // that's the only remaining option from an SSH session.
      if (/DISPLAY|WAYLAND/i.test(errMsg)) setPasteOpen(true);
    } finally {
      setBusy(null);
    }
  };

  const importCookies = async () => {
    setBusy('paste');
    setErr(null);
    setMsg(null);
    let parsed: any;
    try {
      parsed = JSON.parse(cookiesText);
    } catch (e: any) {
      setErr(`Invalid JSON: ${e?.message || e}`);
      setBusy(null);
      return;
    }
    // Accept either a raw array, or {cookies: [...]}, or Playwright storage_state
    const arr = Array.isArray(parsed) ? parsed
              : Array.isArray(parsed?.cookies) ? parsed.cookies
              : null;
    if (!arr) {
      setErr('Expected a JSON array of cookies (or an object with a "cookies" array).');
      setBusy(null);
      return;
    }
    try {
      const r = await api.grokImportCookies(arr);
      setMsg(`Imported ${r.imported} cookie${r.imported === 1 ? '' : 's'}` +
             (r.ignored ? ` (${r.ignored} ignored — wrong domain)` : '') +
             ` — profile is now ${r.status.state}.`);
      setCookiesText('');
      setPasteOpen(false);
      await onRefresh();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="rounded-lg border border-zinc-800 p-5 space-y-3">
      <div>
        <div className="text-base font-semibold flex items-center gap-2 flex-wrap">
          Grok browser session
          <span className={
            'text-[10px] font-medium uppercase tracking-wider px-2 py-0.5 rounded-full border ' +
            stateStyle.color
          }>
            {stateStyle.label}
          </span>
        </div>
        <div className="text-xs text-zinc-500 mt-1">
          Grok video generation drives the grok.com UI via a persistent Chromium profile.
          Anonymous sessions can submit prompts but the share-URL CDN returns 403 on download.
        </div>
      </div>

      {/* Status grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
        <div className="text-zinc-500">Profile path</div>
        <div className="font-mono text-zinc-300 truncate" title={grok.profile_path}>{grok.profile_path}</div>

        <div className="text-zinc-500">Profile age</div>
        <div className="font-mono text-zinc-300">{grok.profile_age_s === null ? 'n/a' : ago(grok.profile_age_s)}</div>

        <div className="text-zinc-500">Cookies</div>
        <div className="font-mono text-zinc-300">
          {grok.cookies_count} ({grok.host_count} host{grok.host_count === 1 ? '' : 's'})
          {grok.cookies_age_s !== null && (
            <span className="text-zinc-500"> · last write {ago(grok.cookies_age_s)}</span>
          )}
        </div>

        <div className="text-zinc-500">Session cookies</div>
        <div className="font-mono text-zinc-300">
          {grok.session_cookies.length === 0
            ? <span className="text-amber-300">none — not authenticated</span>
            : grok.session_cookies.join(', ')}
        </div>
      </div>

      {grok.state === 'anonymous' && (
        <div className="rounded border border-amber-500/30 bg-amber-500/10 text-amber-200 text-xs p-2.5">
          ⚠ Profile exists but isn't logged in. Generation may submit but share URLs will 403.
          Click <b>Login</b> below to open a Chromium window where you can sign in.
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          onClick={login}
          disabled={busy !== null}
          className="px-3 py-1.5 rounded-md bg-emerald-500/20 text-emerald-200 border border-emerald-500/40
                     hover:bg-emerald-500/30 text-xs font-medium disabled:opacity-50"
        >
          {busy === 'login' ? 'Launching…' : (grok.logged_in ? 'Re-login' : 'Login (headed)')}
        </button>
        <button
          type="button"
          onClick={() => setPasteOpen(o => !o)}
          disabled={busy !== null}
          className="px-3 py-1.5 rounded-md bg-zinc-800 text-zinc-200 border border-zinc-700
                     hover:border-emerald-500/40 hover:text-emerald-300 text-xs font-medium disabled:opacity-50"
        >
          {pasteOpen ? 'Cancel paste' : 'Paste cookies (no display)'}
        </button>
        {grok.state !== 'missing' && (
          <button
            type="button"
            onClick={logout}
            disabled={busy !== null}
            className="px-3 py-1.5 rounded-md bg-red-500/15 text-red-200 border border-red-500/40
                       hover:bg-red-500/25 text-xs font-medium disabled:opacity-50"
          >
            {busy === 'logout' ? 'Wiping…' : 'Logout (wipe profile)'}
          </button>
        )}
        <button
          type="button"
          onClick={() => onRefresh()}
          disabled={busy !== null}
          className="px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300
                     hover:border-zinc-500 text-xs disabled:opacity-50"
        >
          Refresh status
        </button>
      </div>

      {/* Cookie paste fallback for headless / SSH setups */}
      {pasteOpen && (
        <div className="rounded border border-zinc-700 bg-zinc-900/60 p-3 space-y-2">
          <div className="text-xs text-zinc-300 font-semibold">Import cookies from another browser</div>
          <ol className="text-[11px] text-zinc-400 list-decimal pl-4 space-y-0.5">
            <li>Log in to <code className="text-zinc-300">grok.com</code> on any machine.</li>
            <li>Install the <a className="text-emerald-300 underline-offset-2 hover:underline"
                                href="https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm"
                                target="_blank" rel="noreferrer">Cookie-Editor</a> extension
                (or any cookie exporter).</li>
            <li>Open it while on grok.com → <b>Export</b> → <b>Export as JSON</b>.</li>
            <li>Paste the JSON below. We'll keep only grok.com / x.ai / x.com cookies.</li>
          </ol>
          <textarea
            value={cookiesText}
            onChange={e => setCookiesText(e.target.value)}
            placeholder='[{"name":"sso","value":"...","domain":".grok.com",...}, ...]'
            className="w-full h-32 px-2 py-1.5 bg-zinc-950 border border-zinc-700 rounded
                       font-mono text-[11px] text-zinc-200
                       focus:outline-none focus:border-emerald-500 resize-y"
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={importCookies}
              disabled={busy !== null || !cookiesText.trim()}
              className="px-3 py-1.5 rounded-md bg-emerald-500/20 text-emerald-200 border border-emerald-500/40
                         hover:bg-emerald-500/30 text-xs font-medium disabled:opacity-50"
            >
              {busy === 'paste' ? 'Importing…' : 'Import cookies'}
            </button>
            <span className="text-[10px] text-zinc-500">
              Cookies are written into the local Chromium profile via headless Playwright.
            </span>
          </div>
        </div>
      )}

      {msg && <div className="text-xs text-emerald-300">{msg}</div>}
      {err && (
        <pre className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2
                        whitespace-pre-wrap font-mono">{err}</pre>
      )}
    </section>
  );
}
