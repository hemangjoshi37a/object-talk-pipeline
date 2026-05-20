import { useEffect, useRef, useState } from 'react';
import { STEP_LABEL, STEP_ORDER, type Run, type RunStatus, type StepName } from '../api';
import { StepBar } from './StepBar';
import { LogPanel } from './LogPanel';
import { ArtifactsPanel } from './ArtifactsPanel';

// --- Status badge styling -------------------------------------------------
const statusBadge: Record<RunStatus, { label: string; classes: string; dot: string }> = {
  idle:      { label: 'Idle',      classes: 'bg-zinc-800 text-zinc-300 border-zinc-700',                  dot: 'bg-zinc-500' },
  running:   { label: 'Running',   classes: 'bg-amber-500/15 text-amber-200 border-amber-500/50',        dot: 'bg-amber-400 animate-pulse' },
  done:      { label: 'Done',      classes: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40', dot: 'bg-emerald-400' },
  error:     { label: 'Error',     classes: 'bg-red-500/15 text-red-300 border-red-500/50',             dot: 'bg-red-400' },
  cancelled: { label: 'Cancelled', classes: 'bg-zinc-700/40 text-zinc-300 border-zinc-600',             dot: 'bg-zinc-400' },
};

// Compact "X ago" formatter. Falls back to a date string for very old timestamps.
function formatRelative(unixSec: number, now: number): string {
  const diff = Math.max(0, Math.floor((now - unixSec * 1000) / 1000));
  if (diff < 5) return 'just now';
  if (diff < 60) return `${diff}s ago`;
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(unixSec * 1000).toLocaleDateString();
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) {
      // Still refresh occasionally so stale views update.
      const id = setInterval(() => setNow(Date.now()), 60_000);
      return () => clearInterval(id);
    }
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

export function RunView({
  run, logs, onCancel, onRetry,
}: {
  run: Run;
  logs: string[];
  onCancel: () => Promise<void>;
  onRetry: (from: StepName) => Promise<void>;
}) {
  const [retryOpen, setRetryOpen] = useState(false);
  // Default: collapse logs for finished runs where they'd be empty; show them for live runs
  const [logsCollapsed, setLogsCollapsed] = useState(!run.is_active && logs.length === 0);
  // When a run goes from idle to active, auto-show logs
  useEffect(() => {
    if (run.is_active) setLogsCollapsed(false);
  }, [run.is_active]);

  // Close the retry dropdown on outside click / Escape.
  const retryRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!retryOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (retryRef.current && !retryRef.current.contains(e.target as Node)) setRetryOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setRetryOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [retryOpen]);

  const now = useNow(run.is_active);
  const badge = statusBadge[run.status] ?? statusBadge.idle;
  const updatedAgo = formatRelative(run.updated_at, now);

  // Suggest a sensible default for the retry dropdown: the current/last step.
  const suggestedRetry: StepName = run.current_step ?? 'scripts';

  const btnBase =
    'inline-flex items-center gap-1.5 h-9 px-3 text-sm font-medium rounded-md border transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-offset-0 focus:ring-zinc-600';
  const btnNeutral = `${btnBase} bg-zinc-900 border-zinc-700 text-zinc-200 hover:bg-zinc-800 hover:border-zinc-600`;
  const btnDanger  = `${btnBase} bg-red-500/15 border-red-500/40 text-red-200 hover:bg-red-500/25 hover:border-red-500/60 focus:ring-red-500/40`;

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {/* ============== Header ============== */}
      <div className="px-6 py-4 border-b border-zinc-800 flex items-start gap-4">
        <div className="flex-1 min-w-0">
          {/* Title row: subject + live dot */}
          <div className="flex items-center gap-2.5 min-w-0">
            <h2 className="text-lg font-semibold text-zinc-100 truncate" title={run.subject}>
              {run.subject}
            </h2>
            {run.is_active && (
              <span
                className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/40 text-emerald-300 text-[10px] font-semibold uppercase tracking-wider shrink-0"
                title="Worker is running"
              >
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
                </span>
                Live
              </span>
            )}
          </div>

          {/* Meta row: status badge + id + updated */}
          <div className="mt-1.5 flex items-center gap-2 flex-wrap text-xs text-zinc-500">
            <span
              className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] font-semibold uppercase tracking-wider ${badge.classes}`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${badge.dot}`} />
              {badge.label}
            </span>
            <span className="text-zinc-600">·</span>
            <span className="font-mono text-zinc-500 truncate" title={run.id}>
              {run.id}
            </span>
            <span className="text-zinc-600">·</span>
            <span title={new Date(run.updated_at * 1000).toLocaleString()}>
              updated {updatedAgo}
            </span>
            {run.youtube_url && (
              <>
                <span className="text-zinc-600">·</span>
                <a
                  href={run.youtube_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-emerald-400 hover:text-emerald-300 underline-offset-2 hover:underline truncate"
                  title={run.youtube_url}
                >
                  YouTube ↗
                </a>
              </>
            )}
          </div>
        </div>

        {/* ============== Action toolbar ============== */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setLogsCollapsed(c => !c)}
            className={btnNeutral}
            title={logsCollapsed ? 'Show log panel' : 'Hide log panel for more artifact room'}
            aria-pressed={!logsCollapsed}
          >
            <span aria-hidden>{logsCollapsed ? '▸' : '▾'}</span>
            {logsCollapsed ? 'Show logs' : 'Hide logs'}
          </button>

          {run.is_active ? (
            <button onClick={onCancel} className={btnDanger} title="Stop the running worker">
              <span aria-hidden>■</span>
              Cancel
            </button>
          ) : (
            <div className="relative" ref={retryRef}>
              <button
                onClick={() => setRetryOpen(o => !o)}
                className={btnNeutral}
                aria-haspopup="menu"
                aria-expanded={retryOpen}
                title="Re-run from a specific step"
              >
                <span aria-hidden>↻</span>
                Retry from…
                <span aria-hidden className="text-zinc-500 text-[10px]">▾</span>
              </button>
              {retryOpen && (
                <div
                  role="menu"
                  className="absolute right-0 mt-1.5 z-20 bg-zinc-900 border border-zinc-700 rounded-md shadow-2xl py-1 min-w-[200px]"
                >
                  <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-zinc-500 font-semibold">
                    Restart from step
                  </div>
                  {STEP_ORDER.map((step, idx) => {
                    const isSuggested = step === suggestedRetry;
                    return (
                      <button
                        key={step}
                        role="menuitem"
                        onClick={() => {
                          setRetryOpen(false);
                          onRetry(step);
                        }}
                        className="w-full text-left px-3 py-1.5 text-sm hover:bg-zinc-800 flex items-center gap-2.5 group"
                      >
                        <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-zinc-800 group-hover:bg-zinc-700 text-[11px] font-bold text-zinc-300 border border-zinc-700">
                          {idx + 1}
                        </span>
                        <span className="flex-1 text-zinc-200">{STEP_LABEL[step]}</span>
                        {isSuggested && (
                          <span className="text-[10px] uppercase tracking-wider text-amber-300/90 font-semibold">
                            suggested
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ============== Step bar ============== */}
      <div className="px-6 py-4 border-b border-zinc-800 bg-zinc-950/40">
        <StepBar run={run} />
      </div>

      {/* ============== Body (untouched layout) ============== */}
      <div className="flex-1 flex gap-4 p-4 overflow-hidden">
        {!logsCollapsed && (
          <div className="w-[380px] shrink-0">
            <LogPanel logs={logs} />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <ArtifactsPanel run={run} />
        </div>
      </div>
    </div>
  );
}
