import { useEffect, useState } from 'react';
import { api, type RunOptions, type VideoProvider, type ComfyuiEngine } from '../api';
import { ClipLengthControls } from './ClipLengthControls';

const PLACEHOLDERS = [
  'e.g. smart factory automation',
  'e.g. how black holes bend time',
  'e.g. why honeybees do a waggle dance',
  'e.g. the history of the silk road',
  'e.g. how CPUs actually multiply numbers',
];

const STEPS = [
  { n: 1, label: 'Scripts', glyph: '✎' },
  { n: 2, label: 'Images', glyph: '✦' },
  { n: 3, label: 'Videos', glyph: '▶' },
  { n: 4, label: 'Merge', glyph: '⎌' },
  { n: 5, label: 'YouTube', glyph: '▲' },
];

export function NewRunForm({
  subject, onSubjectChange, onSubmit,
}: {
  subject: string;
  onSubjectChange: (s: string) => void;
  onSubmit: (opts: RunOptions) => Promise<void>;
}) {
  const [privacy, setPrivacy] = useState<'public' | 'unlisted' | 'private'>('public');
  const [headless, setHeadless] = useState(false);
  const [skipUpload, setSkipUpload] = useState(false);
  const [parallel, setParallel] = useState(false);
  const [skipImages, setSkipImages] = useState(false);
  const [clipCount, setClipCount] = useState(5);
  const [clipDurationS, setClipDurationS] = useState(10);
  const [maxWords, setMaxWords] = useState<number | null>(null);
  const [videoProvider, setVideoProvider] = useState<VideoProvider>('grok');
  const [comfyuiEngine, setComfyuiEngine] = useState<ComfyuiEngine>(
    (localStorage.getItem('comfyui_engine') as ComfyuiEngine) || 'ltx'
  );
  const [comfyuiReachable, setComfyuiReachable] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [placeholderIdx, setPlaceholderIdx] = useState(0);

  useEffect(() => {
    const id = setInterval(() => {
      setPlaceholderIdx(i => (i + 1) % PLACEHOLDERS.length);
    }, 3000);
    return () => clearInterval(id);
  }, []);

  // Seed provider from the Settings-page default the first time this form mounts
  useEffect(() => {
    api.getSettings().then(s => {
      if (s?.video_provider === 'grok' || s?.video_provider === 'comfyui') {
        setVideoProvider(s.video_provider);
      }
      // Engine: prefer localStorage, then settings default
      if (!localStorage.getItem('comfyui_engine') &&
          ['ltx', 'wan', 'wan_s2v'].includes(s?.comfyui?.engine)) {
        setComfyuiEngine(s.comfyui.engine);
      }
      setComfyuiReachable(!!s?.comfyui?.reachable);
    }).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!subject.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      localStorage.setItem('video_provider', videoProvider);
      localStorage.setItem('comfyui_engine', comfyuiEngine);
      await onSubmit({
        subject: subject.trim(),
        privacy,
        headless,
        skip_upload: skipUpload,
        parallel,
        video_provider: videoProvider,
        comfyui_engine: videoProvider === 'comfyui' ? comfyuiEngine : undefined,
        skip_images: skipImages,
        clip_count: clipCount,
        clip_duration_s: clipDurationS,
        max_words: maxWords,
      });
      onSubjectChange('');
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <form onSubmit={submit} className="w-full max-w-xl space-y-7">
        {/* Heading */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-[11px] font-medium uppercase tracking-wider">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Auto
            </span>
            <h1 className="text-2xl font-semibold">New run</h1>
          </div>
          <p className="text-sm text-zinc-500">
            Generates Pixar-style scripts, images and videos, then publishes a YouTube Short — hands-off.
          </p>
        </div>

        {/* Pipeline preview */}
        <div className="flex items-center justify-between gap-1.5">
          {STEPS.map((s, i) => (
            <div key={s.n} className="flex items-center flex-1 last:flex-none">
              <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-zinc-900/60 border border-zinc-800 text-xs text-zinc-300 flex-1 min-w-0">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-emerald-500/10 text-emerald-400 text-[10px] font-semibold flex-shrink-0">
                  {s.n}
                </span>
                <span className="text-zinc-400 flex-shrink-0">{s.glyph}</span>
                <span className="truncate">{s.label}</span>
              </div>
              {i < STEPS.length - 1 && (
                <span className="text-zinc-700 px-1 flex-shrink-0" aria-hidden>›</span>
              )}
            </div>
          ))}
        </div>

        {/* Subject input */}
        <div>
          <label className="block text-sm font-medium mb-2 text-zinc-300">
            Subject
          </label>
          <input
            value={subject}
            onChange={e => onSubjectChange(e.target.value)}
            placeholder={PLACEHOLDERS[placeholderIdx]}
            autoFocus
            list="subject-suggestions"
            className="w-full px-4 py-3 text-base bg-zinc-900 border border-zinc-700 rounded-md
                       focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500/40
                       placeholder:text-zinc-600 transition"
          />
          <datalist id="subject-suggestions">
            {PLACEHOLDERS.map(p => (
              <option key={p} value={p.replace(/^e\.g\.\s*/, '')} />
            ))}
          </datalist>
        </div>

        {/* Clip length controls */}
        <ClipLengthControls
          clipCount={clipCount} setClipCount={setClipCount}
          clipDurationS={clipDurationS} setClipDurationS={setClipDurationS}
          maxWords={maxWords} setMaxWords={setMaxWords}
          accent="emerald"
        />

        {/* Video provider */}
        <div>
          <label className="block text-sm font-medium mb-2 text-zinc-300">
            Video generator
            <span className="text-zinc-500 font-normal"> — which backend renders the {clipCount} clips</span>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setVideoProvider('grok')}
              className={
                'px-3 py-2.5 rounded-md border text-left transition ' +
                (videoProvider === 'grok'
                  ? 'border-emerald-500/60 bg-emerald-500/10 text-zinc-100'
                  : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600')
              }
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">Grok Imagine</span>
                <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded
                                 bg-zinc-800 text-zinc-400">cloud</span>
              </div>
              <div className="text-[11px] text-zinc-500 mt-0.5">
                10s/clip, 720p I2V, Hindi lip-sync. Premium subscription.
              </div>
            </button>
            <button
              type="button"
              onClick={() => setVideoProvider('comfyui')}
              className={
                'px-3 py-2.5 rounded-md border text-left transition ' +
                (videoProvider === 'comfyui'
                  ? 'border-emerald-500/60 bg-emerald-500/10 text-zinc-100'
                  : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600')
              }
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">ComfyUI (LTX-2.3)</span>
                <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded
                                 bg-zinc-800 text-zinc-400">local</span>
                {comfyuiReachable === false && (
                  <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded
                                   bg-red-500/10 text-red-300 border border-red-500/30">offline</span>
                )}
              </div>
              <div className="text-[11px] text-zinc-500 mt-0.5">
                T2V on your GPU. No quota, no cost. Hindi quality varies.
              </div>
            </button>
          </div>
          {videoProvider === 'comfyui' && comfyuiReachable === false && (
            <div className="mt-2 text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2">
              ComfyUI is not reachable at the configured URL. Open <b>Settings → ComfyUI</b> to
              set the right URL, or pick Grok above.
            </div>
          )}
          {videoProvider === 'comfyui' && (
            <div className="mt-2 rounded-md border border-zinc-800 bg-zinc-900/40 p-2.5">
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1.5">
                ComfyUI engine
              </div>
              <div className="grid grid-cols-3 gap-1.5">
                <button
                  type="button"
                  onClick={() => setComfyuiEngine('ltx')}
                  className={
                    'px-2 py-1.5 rounded text-left text-xs transition ' +
                    (comfyuiEngine === 'ltx'
                      ? 'border border-emerald-500/60 bg-emerald-500/10 text-zinc-100'
                      : 'border border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600')
                  }
                >
                  <div className="font-medium">LTX-2.3</div>
                  <div className="text-[10px] text-zinc-500 mt-0.5">
                    Fast diffusion. Text-only audio.
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => setComfyuiEngine('wan')}
                  className={
                    'px-2 py-1.5 rounded text-left text-xs transition ' +
                    (comfyuiEngine === 'wan'
                      ? 'border border-emerald-500/60 bg-emerald-500/10 text-zinc-100'
                      : 'border border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600')
                  }
                >
                  <div className="font-medium">Wan TI2V 5B</div>
                  <div className="text-[10px] text-zinc-500 mt-0.5">
                    Turbo Q8. ~1 min/clip. No lip-sync.
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => setComfyuiEngine('wan_s2v')}
                  className={
                    'px-2 py-1.5 rounded text-left text-xs transition ' +
                    (comfyuiEngine === 'wan_s2v'
                      ? 'border border-emerald-500/60 bg-emerald-500/10 text-zinc-100'
                      : 'border border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600')
                  }
                >
                  <div className="font-medium">Wan S2V 14B</div>
                  <div className="text-[10px] text-zinc-500 mt-0.5">
                    Q3 GGUF. Real lip-sync. Slower.
                  </div>
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Primary option: privacy */}
        <div>
          <label className="block text-sm font-medium mb-2 text-zinc-300">YouTube privacy</label>
          <select
            value={privacy}
            onChange={e => setPrivacy(e.target.value as any)}
            disabled={skipUpload}
            className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-md
                       focus:outline-none focus:border-emerald-500
                       disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            <option value="public">Public</option>
            <option value="unlisted">Unlisted</option>
            <option value="private">Private</option>
          </select>
        </div>

        {/* Advanced */}
        <div className="border border-zinc-800 rounded-md bg-zinc-900/30">
          <button
            type="button"
            onClick={() => setAdvancedOpen(o => !o)}
            className="w-full flex items-center justify-between px-3 py-2 text-sm text-zinc-300 hover:text-zinc-100 transition"
          >
            <span className="flex items-center gap-2">
              <span className="text-zinc-500">⚙</span>
              Advanced
            </span>
            <span className={`text-zinc-500 transition-transform ${advancedOpen ? 'rotate-180' : ''}`}>⌄</span>
          </button>
          {advancedOpen && (
            <div className="px-3 pb-3 pt-1 space-y-2 border-t border-zinc-800">
              <label className="flex items-center gap-2 text-sm cursor-pointer hover:text-zinc-100 transition">
                <input
                  type="checkbox"
                  checked={headless}
                  onChange={e => setHeadless(e.target.checked)}
                  className="accent-emerald-500"
                />
                <span>Run Grok browser headless</span>
                <span className="text-xs text-zinc-500">— hides the automation window</span>
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer hover:text-zinc-100 transition">
                <input
                  type="checkbox"
                  checked={skipUpload}
                  onChange={e => setSkipUpload(e.target.checked)}
                  className="accent-emerald-500"
                />
                <span>Skip YouTube upload</span>
                <span className="text-xs text-zinc-500">— stop after merge</span>
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer hover:text-zinc-100 transition">
                <input
                  type="checkbox"
                  checked={parallel}
                  onChange={e => setParallel(e.target.checked)}
                  className="accent-amber-500"
                />
                <span>Parallel generation</span>
                <span className="text-xs text-zinc-500">
                  — images at once + multi-tab video gen. Faster but may stress Grok rate limits.
                </span>
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer hover:text-zinc-100 transition">
                <input
                  type="checkbox"
                  checked={skipImages}
                  onChange={e => setSkipImages(e.target.checked)}
                  className="accent-sky-500"
                />
                <span>Skip image generation (text-only video)</span>
                <span className="text-xs text-zinc-500">
                  — bypass Gemini image step. Character description goes straight into the video prompt.
                </span>
              </label>
            </div>
          )}
        </div>

        {/* Estimate badges */}
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="px-2 py-0.5 rounded-full bg-zinc-900 border border-zinc-800 text-zinc-400">
            ⏱ ~5 min
          </span>
          <span className="px-2 py-0.5 rounded-full bg-zinc-900 border border-zinc-800 text-zinc-400">
            💰 ~$0.25 Gemini
          </span>
          <span className="px-2 py-0.5 rounded-full bg-zinc-900 border border-zinc-800 text-zinc-400">
            🎬 Grok via subscription
          </span>
          <span className="px-2 py-0.5 rounded-full bg-zinc-900 border border-zinc-800 text-zinc-400">
            📺 1,600 YT quota
          </span>
        </div>

        {error && (
          <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded p-2">
            {error}
          </div>
        )}

        {/* Run button */}
        <div className="pt-1">
          <button
            type="submit"
            disabled={submitting || !subject.trim()}
            className="group relative w-full sm:w-auto px-6 py-3 rounded-md font-medium
                       bg-gradient-to-b from-emerald-400 to-emerald-500 text-zinc-950
                       shadow-lg shadow-emerald-500/20
                       ring-1 ring-emerald-400/50
                       hover:from-emerald-300 hover:to-emerald-400 hover:shadow-emerald-500/30
                       disabled:from-zinc-800 disabled:to-zinc-800 disabled:text-zinc-500
                       disabled:ring-zinc-700 disabled:shadow-none disabled:cursor-not-allowed
                       transition-all"
          >
            <span className="inline-flex items-center gap-2">
              {submitting ? (
                <>
                  <span className="inline-block w-3 h-3 rounded-full border-2 border-zinc-950/40 border-t-zinc-950 animate-spin" />
                  Starting…
                </>
              ) : (
                <>
                  <span>▶</span>
                  Run pipeline
                </>
              )}
            </span>
          </button>
        </div>
      </form>
    </div>
  );
}
