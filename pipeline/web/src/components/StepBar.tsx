import { STEP_LABEL, STEP_ORDER, type Run, type StepName } from '../api';

type StepUiState = 'pending' | 'next' | 'active' | 'done' | 'error';

function stepState(run: Run, step: StepName): StepUiState {
  const idx = STEP_ORDER.indexOf(step);
  const curIdx = run.current_step ? STEP_ORDER.indexOf(run.current_step) : -1;
  if (run.status === 'error' && curIdx === idx) return 'error';
  if (curIdx === idx && run.is_active) return 'active';
  if (curIdx > idx) return 'done';
  // Derive done state from artifacts when run is not active
  if (!run.is_active) {
    if (step === 'scripts' && run.artifacts.scripts_json) return 'done';
    if (step === 'images' && run.artifacts.images.length >= 5) return 'done';
    if (step === 'videos' && run.artifacts.videos.length >= 5) return 'done';
    if (step === 'merge' && run.artifacts.merged) return 'done';
    if (step === 'upload' && run.youtube_url) return 'done';
  }
  // Step that's immediately upcoming (next in line while a run is active)
  if (run.is_active && curIdx >= 0 && idx === curIdx + 1) return 'next';
  return 'pending';
}

// Container classes per state — bigger, clearer chips.
const chipStyle: Record<StepUiState, string> = {
  pending: 'bg-zinc-900/60 text-zinc-500 border-zinc-800',
  next:    'bg-zinc-900/80 text-zinc-300 border-zinc-600 border-dashed',
  active:  'bg-amber-500/15 text-amber-200 border-amber-400/70 shadow-[0_0_0_1px_rgba(245,158,11,0.35),0_0_18px_-2px_rgba(245,158,11,0.55)]',
  done:    'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  error:   'bg-red-500/15 text-red-300 border-red-500/50',
};

// Glyph shown inside the round number badge.
const stateGlyph: Record<StepUiState, (n: number) => string> = {
  pending: n => String(n),
  next:    n => String(n),
  active:  n => String(n),
  done:    () => '✓', // ✓
  error:   () => '!',
};

const badgeStyle: Record<StepUiState, string> = {
  pending: 'bg-zinc-800 text-zinc-500 border border-zinc-700',
  next:    'bg-zinc-800 text-zinc-300 border border-zinc-600',
  active:  'bg-amber-500 text-zinc-950 border border-amber-300',
  done:    'bg-emerald-500 text-zinc-950 border border-emerald-400',
  error:   'bg-red-500 text-zinc-950 border border-red-400',
};

const connectorStyle: Record<StepUiState, string> = {
  pending: 'bg-zinc-800',
  next:    'bg-zinc-800',
  active:  'bg-gradient-to-r from-emerald-500 to-amber-500',
  done:    'bg-emerald-500/60',
  error:   'bg-red-500/50',
};

export function StepBar({ run }: { run: Run }) {
  return (
    <div className="flex items-stretch gap-0 w-full">
      {STEP_ORDER.map((step, i) => {
        const st = stepState(run, step);
        const isLast = i === STEP_ORDER.length - 1;
        const showProg = st === 'active' && run.step_progress?.step === step;
        const progPct =
          showProg && run.step_progress && run.step_progress.total > 0
            ? Math.min(100, Math.round((run.step_progress.done / run.step_progress.total) * 100))
            : null;

        return (
          <div key={step} className="flex items-center flex-1 min-w-0 last:flex-none">
            <div
              className={`relative flex items-center gap-2.5 px-3 py-2 rounded-lg border min-w-0 flex-1 transition-colors ${chipStyle[st]}`}
            >
              {/* Number / status badge */}
              <div
                className={`relative shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${badgeStyle[st]}`}
              >
                {stateGlyph[st](i + 1)}
                {st === 'active' && (
                  <span className="absolute inset-0 rounded-full ring-2 ring-amber-400/60 animate-ping" />
                )}
              </div>

              {/* Label + progress */}
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium leading-tight truncate flex items-center gap-1.5">
                  {STEP_LABEL[step]}
                  {st === 'active' && (
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-amber-300/80">
                      running
                    </span>
                  )}
                </div>
                {showProg && run.step_progress ? (
                  <div className="mt-1">
                    <div className="flex items-center justify-between text-[10px] font-mono text-amber-200/90">
                      <span>{run.step_progress.done}/{run.step_progress.total}</span>
                      {progPct !== null && <span>{progPct}%</span>}
                    </div>
                    <div className="mt-0.5 h-1 rounded-full bg-amber-950/50 overflow-hidden">
                      <div
                        className="h-full bg-amber-400 transition-all duration-300"
                        style={{ width: `${progPct ?? 0}%` }}
                      />
                    </div>
                  </div>
                ) : (
                  <div className="text-[10px] uppercase tracking-wider text-zinc-500/80 leading-tight">
                    {st === 'done'    && 'complete'}
                    {st === 'error'   && 'failed'}
                    {st === 'next'    && 'up next'}
                    {st === 'pending' && 'pending'}
                  </div>
                )}
              </div>
            </div>

            {/* Connector arrow */}
            {!isLast && (
              <div className="flex items-center px-1 shrink-0" aria-hidden>
                <div className={`h-0.5 w-4 sm:w-6 ${connectorStyle[st]}`} />
                <div
                  className={`text-base leading-none -ml-0.5 ${
                    st === 'done'
                      ? 'text-emerald-500/70'
                      : st === 'active'
                      ? 'text-amber-400'
                      : st === 'error'
                      ? 'text-red-500/70'
                      : 'text-zinc-700'
                  }`}
                >
                  {'▸'}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
