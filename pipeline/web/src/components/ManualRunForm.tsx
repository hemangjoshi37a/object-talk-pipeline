import { useEffect, useState } from 'react';
import { api, type VideoProvider, type ComfyuiEngine } from '../api';
import { ClipLengthControls } from './ClipLengthControls';

const EXAMPLE_SUBJECTS = ['monsoon snacks', 'street food in Delhi', 'home remedies for cough'];

export function ManualRunForm({
  subject, onSubjectChange, onSubmit,
}: {
  subject: string;
  onSubjectChange: (s: string) => void;
  onSubmit: (subject: string, skipImages: boolean, comfyuiEngine?: ComfyuiEngine,
             clipCount?: number, clipDurationS?: number,
             maxWords?: number | null) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [videoProvider, setVideoProvider] = useState<VideoProvider>(
    (localStorage.getItem('video_provider') as VideoProvider) || 'grok'
  );
  const [comfyuiEngine, setComfyuiEngine] = useState<ComfyuiEngine>(
    (localStorage.getItem('comfyui_engine') as ComfyuiEngine) || 'ltx'
  );
  const [comfyuiReachable, setComfyuiReachable] = useState<boolean | null>(null);
  const [skipImages, setSkipImages] = useState(false);
  const [clipCount, setClipCount] = useState(5);
  const [clipDurationS, setClipDurationS] = useState(10);
  const [maxWords, setMaxWords] = useState<number | null>(null);

  useEffect(() => {
    api.getSettings().then(s => {
      if (!localStorage.getItem('video_provider') &&
          (s?.video_provider === 'grok' || s?.video_provider === 'comfyui')) {
        setVideoProvider(s.video_provider);
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
      // Sticky choice for the per-clip Generate buttons in the run view
      localStorage.setItem('video_provider', videoProvider);
      localStorage.setItem('comfyui_engine', comfyuiEngine);
      await onSubmit(subject.trim(), skipImages,
                     videoProvider === 'comfyui' ? comfyuiEngine : undefined,
                     clipCount, clipDurationS, maxWords);
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setSubmitting(false);
    }
  };

  // Steps in the pipeline. `auto` indicates whether this step runs automatically
  // in manual mode (only scripts do); the rest require a user click.
  const steps: { label: string; auto: boolean }[] = [
    { label: 'Scripts', auto: true },
    { label: 'Images', auto: false },
    { label: 'Clips', auto: false },
    { label: 'Merge', auto: false },
    { label: 'Upload', auto: false },
  ];

  return (
    <div className="px-8 pt-8 pb-4">
      <div className="max-w-3xl mx-auto space-y-5">
        {/* Heading + mode badge */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h1 className="text-2xl font-semibold">Manual run</h1>
              <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded
                               bg-amber-500/15 text-amber-400 border border-amber-500/30 font-medium">
                Step-by-step
              </span>
            </div>
            <p className="text-sm text-zinc-500">
              Scripts generate automatically. You then click through images, clips, merge and upload
              at your own pace — review or skip any step.
            </p>
          </div>
          <div className="text-right text-[11px] text-zinc-500 leading-tight shrink-0 pt-1">
            <div className="text-zinc-400">~5 sec</div>
            <div>for scripts</div>
            <div className="mt-1 text-zinc-600">then it's up to you</div>
          </div>
        </div>

        {/* Pipeline stepper — visual distinction between auto (amber) and manual (click) steps */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-2 px-1">
            Pipeline
          </div>
          <div className="flex items-stretch gap-1.5">
            {steps.map((s, i) => (
              <div key={s.label} className="flex items-stretch flex-1 min-w-0">
                <div
                  className={
                    'flex-1 min-w-0 px-2 py-2 rounded-md border text-center ' +
                    (s.auto
                      ? 'border-amber-500/40 bg-amber-500/10 text-amber-300'
                      : 'border-zinc-700 bg-zinc-900 text-zinc-300')
                  }
                >
                  <div className="text-xs font-medium truncate">{s.label}</div>
                  <div className={'text-[10px] mt-0.5 ' + (s.auto ? 'text-amber-400/80' : 'text-zinc-500')}>
                    {s.auto ? 'auto' : '⏸ click'}
                  </div>
                </div>
                {i < steps.length - 1 && (
                  <div className="flex items-center px-0.5 text-zinc-600 text-xs select-none">→</div>
                )}
              </div>
            ))}
          </div>
          <div className="flex items-center gap-3 mt-2 px-1 text-[10px] text-zinc-500">
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-sm bg-amber-500/60" /> auto
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-sm bg-zinc-700" /> you trigger
            </span>
            <span className="ml-auto text-zinc-600">
              Tip: Auto mode runs all 5 steps end-to-end. Manual gives you a checkpoint at each step.
            </span>
          </div>
        </div>

        {/* When-to-use callout */}
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.04] p-3">
          <div className="text-xs font-medium text-amber-300 mb-1.5">When to use manual mode</div>
          <ul className="text-xs text-zinc-400 space-y-1 list-none">
            <li className="flex gap-2"><span className="text-amber-500/70">·</span>Review or edit each script before spending time on images.</li>
            <li className="flex gap-2"><span className="text-amber-500/70">·</span>Regenerate or skip clips you don't like without redoing the whole run.</li>
            <li className="flex gap-2"><span className="text-amber-500/70">·</span>Merge now but upload later (e.g. wait for a better posting time).</li>
          </ul>
        </div>

        {/* Video provider — same as Auto, sticky for the per-clip Generate buttons */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-2 px-1">
            Video generator (used when you click Generate on a clip)
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setVideoProvider('grok')}
              className={
                'px-3 py-2.5 rounded-md border text-left transition ' +
                (videoProvider === 'grok'
                  ? 'border-amber-500/60 bg-amber-500/10 text-zinc-100'
                  : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600')
              }
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">Grok Imagine</span>
                <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded
                                 bg-zinc-800 text-zinc-400">cloud</span>
              </div>
              <div className="text-[11px] text-zinc-500 mt-0.5">
                I2V w/ Hindi lip-sync. Premium subscription.
              </div>
            </button>
            <button
              type="button"
              onClick={() => setVideoProvider('comfyui')}
              className={
                'px-3 py-2.5 rounded-md border text-left transition ' +
                (videoProvider === 'comfyui'
                  ? 'border-amber-500/60 bg-amber-500/10 text-zinc-100'
                  : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600')
              }
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">ComfyUI</span>
                <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded
                                 bg-zinc-800 text-zinc-400">local</span>
                {comfyuiReachable === false && (
                  <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded
                                   bg-red-500/10 text-red-300 border border-red-500/30">offline</span>
                )}
              </div>
              <div className="text-[11px] text-zinc-500 mt-0.5">
                T2V/I2V on your GPU. No quota, no cost.
              </div>
            </button>
          </div>
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
                      ? 'border border-amber-500/60 bg-amber-500/10 text-zinc-100'
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
                      ? 'border border-amber-500/60 bg-amber-500/10 text-zinc-100'
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
                      ? 'border border-amber-500/60 bg-amber-500/10 text-zinc-100'
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

        {/* Clip length controls */}
        <ClipLengthControls
          clipCount={clipCount} setClipCount={setClipCount}
          clipDurationS={clipDurationS} setClipDurationS={setClipDurationS}
          maxWords={maxWords} setMaxWords={setMaxWords}
          accent="amber"
        />

        {/* Advanced — skip image gen */}
        <label className="flex items-center gap-2 text-sm cursor-pointer hover:text-zinc-100 transition
                          rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2">
          <input
            type="checkbox"
            checked={skipImages}
            onChange={e => setSkipImages(e.target.checked)}
            className="accent-sky-500"
          />
          <span className="font-medium">Skip image generation</span>
          <span className="text-xs text-zinc-500">
            — bypass Gemini image step. The run view will hide the Images strip
            and feed the character description straight into video generation.
          </span>
        </label>

        {/* Subject form */}
        <form onSubmit={submit} className="space-y-2">
          <label className="block text-sm font-medium text-zinc-300">
            Subject <span className="text-zinc-600 font-normal">— what should the {clipCount} scripts be about?</span>
          </label>
          <div className="flex items-stretch gap-2">
            <input
              value={subject}
              onChange={e => onSubjectChange(e.target.value)}
              placeholder="e.g. monsoon snacks"
              autoFocus
              className="flex-1 px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-md
                         focus:outline-none focus:border-amber-500 placeholder:text-zinc-600"
            />
            <button
              type="submit"
              disabled={submitting || !subject.trim()}
              className="px-5 py-2 bg-amber-500 text-zinc-950 font-medium rounded-md
                         hover:bg-amber-400 disabled:bg-zinc-700 disabled:text-zinc-500
                         disabled:cursor-not-allowed whitespace-nowrap transition"
            >
              {submitting ? 'Generating…' : 'Generate scripts ↓'}
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">
            <span className="text-zinc-600">try:</span>
            {EXAMPLE_SUBJECTS.map(ex => (
              <button
                key={ex}
                type="button"
                onClick={() => onSubjectChange(ex)}
                className="px-1.5 py-0.5 rounded border border-zinc-800 hover:border-amber-500/50
                           hover:text-amber-300 text-zinc-400 transition"
              >
                {ex}
              </button>
            ))}
          </div>
        </form>

        {error && (
          <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded p-2">
            {error}
          </div>
        )}

        <div className="text-[11px] text-zinc-500 italic border-l-2 border-amber-500/40 pl-2">
          After scripts generate, the run opens with empty placeholders for images and clips. Click
          "Generate" on each placeholder, then "Merge" and "Upload" at the bottom.
        </div>
      </div>
    </div>
  );
}
