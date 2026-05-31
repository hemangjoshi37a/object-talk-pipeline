/**
 * Clip count + per-clip duration sliders with total-length readout and
 * YouTube Shorts / Instagram Reels compliance pills. Used in both the Auto
 * and Manual run forms.
 *
 * Format limits we check against:
 *   - YouTube Shorts ≤ 60 s
 *   - Instagram Reels ≤ 90 s (officially up to 3 min but algorithm prefers ≤90)
 *
 * Word budget: ~4 spoken Hindi words per second of clip → shown as a hint.
 */
import type { JSX } from 'react';

const YT_SHORTS_MAX = 60;
const IG_REELS_MAX = 90;
// Calculated default — must match generate_scripts._default_max_words():
// max(10, duration_s * 3 - 5). The UI shows this as a placeholder until the
// user types in an override; an override is forwarded as opts.max_words to
// the backend, which feeds it straight into Gemini as the hard ceiling.
function defaultMaxWords(durationS: number): number {
  return Math.max(10, durationS * 3 - 5);
}

export function ClipLengthControls({
  clipCount, setClipCount,
  clipDurationS, setClipDurationS,
  maxWords, setMaxWords,
  accent = 'emerald',
}: {
  clipCount: number;
  setClipCount: (n: number) => void;
  clipDurationS: number;
  setClipDurationS: (n: number) => void;
  maxWords: number | null;
  setMaxWords: (n: number | null) => void;
  accent?: 'emerald' | 'amber';
}): JSX.Element {
  const totalS = clipCount * clipDurationS;
  const computedDefault = defaultMaxWords(clipDurationS);
  const effectiveMax = maxWords ?? computedDefault;
  const isOverridden = maxWords !== null;
  const fitsYt = totalS <= YT_SHORTS_MAX;
  const fitsIg = totalS <= IG_REELS_MAX;

  const pill = (ok: boolean, label: string, limit: number) => (
    <span
      className={
        'text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded-full border font-medium ' +
        (ok
          ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/40'
          : 'bg-red-500/10 text-red-300 border-red-500/40')
      }
      title={`${label} max ${limit}s`}
    >
      {ok ? '✓' : '✗'} {label} {limit}s
    </span>
  );

  const accentClass = accent === 'amber' ? 'accent-amber-500' : 'accent-emerald-500';

  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/40 p-3 space-y-3">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500">
          Length
        </div>
        <div className="flex items-center gap-1.5">
          {pill(fitsYt, 'YT Short', YT_SHORTS_MAX)}
          {pill(fitsIg, 'IG Reel', IG_REELS_MAX)}
        </div>
      </div>

      {/* Total readout */}
      <div className="flex items-baseline gap-2">
        <div className="text-2xl font-semibold text-zinc-100 tabular-nums">
          {totalS}
          <span className="text-sm text-zinc-500 ml-1">s</span>
        </div>
        <div className="text-xs text-zinc-500">
          total ({clipCount} × {clipDurationS}s)
        </div>
      </div>

      {/* Clip count slider */}
      <div className="grid grid-cols-[1fr_120px] gap-2 items-center">
        <div>
          <label className="block text-xs text-zinc-300">Number of clips</label>
          <div className="text-[10px] text-zinc-500">1–20 (default 5)</div>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="range" min={1} max={20} step={1}
            value={clipCount}
            onChange={e => setClipCount(parseInt(e.target.value))}
            className={`flex-1 ${accentClass}`}
          />
          <span className="text-xs font-mono text-zinc-300 w-6 text-right">{clipCount}</span>
        </div>
      </div>

      {/* Per-clip duration */}
      <div className="grid grid-cols-[1fr_120px] gap-2 items-center">
        <div>
          <label className="block text-xs text-zinc-300">Duration per clip</label>
          <div className="text-[10px] text-zinc-500">
            5–30 s
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="range" min={5} max={30} step={1}
            value={clipDurationS}
            onChange={e => setClipDurationS(parseInt(e.target.value))}
            className={`flex-1 ${accentClass}`}
          />
          <span className="text-xs font-mono text-zinc-300 w-8 text-right">{clipDurationS}s</span>
        </div>
      </div>

      {/* Max words override — default shown as placeholder, value = override */}
      <div className="grid grid-cols-[1fr_120px] gap-2 items-center">
        <div>
          <label className="block text-xs text-zinc-300">
            Max words / script
            {isOverridden && (
              <span className={
                'ml-1.5 text-[9px] uppercase tracking-wider px-1 py-px rounded ' +
                (accent === 'amber'
                  ? 'bg-amber-500/15 text-amber-300 border border-amber-500/40'
                  : 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/40')
              }>
                manual
              </span>
            )}
          </label>
          <div className="text-[10px] text-zinc-500">
            Default {computedDefault} (= duration × 3 − 5) ·
            Gemini hard limit · no trimming
          </div>
        </div>
        <div className="flex items-center gap-1">
          <input
            type="number" min={4} max={120} step={1}
            value={maxWords ?? ''}
            placeholder={String(computedDefault)}
            onChange={e => {
              const v = e.target.value.trim();
              if (v === '') return setMaxWords(null);
              const n = parseInt(v);
              if (Number.isFinite(n)) setMaxWords(Math.max(4, Math.min(120, n)));
            }}
            className="flex-1 px-2 py-1 bg-zinc-900 border border-zinc-700 rounded
                       text-xs font-mono text-zinc-200 text-right
                       focus:outline-none focus:border-emerald-500"
          />
          {isOverridden && (
            <button
              type="button"
              onClick={() => setMaxWords(null)}
              title="Reset to auto"
              className="text-[10px] px-1.5 py-1 rounded border border-zinc-700
                         text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
            >
              auto
            </button>
          )}
        </div>
      </div>
      <div className="text-[10px] text-zinc-500 -mt-1">
        Scripts target ≤ <span className="text-zinc-300 font-mono">{effectiveMax}</span> words
        ({(effectiveMax / clipDurationS).toFixed(1)} wps).
        {effectiveMax / clipDurationS > 3 && (
          <span className="text-amber-400 ml-1">⚠ Above 3 wps — TTS may sound rushed.</span>
        )}
      </div>

      {!fitsIg && (
        <div className="text-[11px] text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded p-1.5">
          ⚠ Total exceeds Instagram Reels limit. Reduce clip count or duration.
        </div>
      )}
    </div>
  );
}
