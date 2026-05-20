import { useMemo, useState } from 'react';
import type { Run, StepName } from '../api';
import { STEP_ORDER } from '../api';

const statusBadge: Record<string, string> = {
  running: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
  done: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
  error: 'bg-red-500/20 text-red-300 border-red-500/40',
  cancelled: 'bg-zinc-500/20 text-zinc-300 border-zinc-500/40',
  idle: 'bg-zinc-700/40 text-zinc-400 border-zinc-600',
};

// How far through STEP_ORDER a run has progressed (0..STEP_ORDER.length).
function stepsCompleted(r: Run): number {
  if (r.status === 'done') return STEP_ORDER.length;
  const cur = r.current_step as StepName | null;
  if (!cur) return 0;
  const idx = STEP_ORDER.indexOf(cur);
  if (idx < 0) return 0;
  // If a sub-progress exists and is 100%, count current step as done too.
  if (r.step_progress && r.step_progress.step === cur && r.step_progress.total > 0
      && r.step_progress.done >= r.step_progress.total) {
    return Math.min(idx + 1, STEP_ORDER.length);
  }
  return idx;
}

function StepDots({ run }: { run: Run }) {
  const completed = stepsCompleted(run);
  const isRunning = run.status === 'running' && run.is_active;
  const isError = run.status === 'error';
  return (
    <div className="flex items-center gap-1" aria-label={`progress ${completed}/${STEP_ORDER.length}`}>
      {STEP_ORDER.map((step, i) => {
        const isDone = i < completed;
        const isCurrent = isRunning && i === completed;
        const base = 'w-1.5 h-1.5 rounded-full';
        let cls = 'bg-zinc-700';
        if (isDone) cls = 'bg-emerald-500';
        else if (isCurrent) cls = 'bg-amber-400 animate-pulse';
        else if (isError && i === completed) cls = 'bg-red-500';
        return <span key={step} className={`${base} ${cls}`} title={step} />;
      })}
    </div>
  );
}

// Day-bucket label for grouping (uses local time, day boundaries at midnight).
function bucketOf(updatedAtSec: number, nowSec: number): 'Today' | 'Yesterday' | 'Earlier' {
  const now = new Date(nowSec * 1000);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const startOfYesterday = startOfToday - 86400;
  if (updatedAtSec >= startOfToday) return 'Today';
  if (updatedAtSec >= startOfYesterday) return 'Yesterday';
  return 'Earlier';
}

export function Sidebar({
  runs, selectedId, mode, onSelect, onNew, onManual, onSettings, onDelete,
}: {
  runs: Run[];
  selectedId: string | null;
  mode: 'auto' | 'manual' | 'settings';
  onSelect: (id: string) => void;
  onNew: () => void;
  onManual: () => void;
  onSettings: () => void;
  onDelete: (id: string) => void;
}) {
  const isLanding = selectedId === null;
  const [query, setQuery] = useState('');
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter(r => r.subject.toLowerCase().includes(q));
  }, [runs, query]);

  // Group filtered runs into buckets, preserving the input order within each bucket.
  const grouped = useMemo(() => {
    const nowSec = Math.floor(Date.now() / 1000);
    const buckets: Record<'Today' | 'Yesterday' | 'Earlier', Run[]> = {
      Today: [], Yesterday: [], Earlier: [],
    };
    for (const r of filtered) buckets[bucketOf(r.updated_at, nowSec)].push(r);
    return buckets;
  }, [filtered]);

  const counts = useMemo(() => {
    const live = runs.filter(r => r.is_active).length;
    const done = runs.filter(r => r.status === 'done').length;
    return { total: runs.length, live, done };
  }, [runs]);

  const showSearch = runs.length >= 5;

  const handleDeleteClick = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (pendingDelete === id) {
      onDelete(id);
      setPendingDelete(null);
    } else {
      setPendingDelete(id);
      // Auto-reset the pending state after 2.5s so it doesn't linger.
      window.setTimeout(() => {
        setPendingDelete(prev => (prev === id ? null : prev));
      }, 2500);
    }
  };

  const renderRun = (r: Run) => (
    <div
      key={r.id}
      onClick={() => onSelect(r.id)}
      className={`group px-3 py-2 border-b border-zinc-900 cursor-pointer flex items-center gap-2 ${
        selectedId === r.id ? 'bg-zinc-800' : 'hover:bg-zinc-900'
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{r.subject}</div>
        <div className="flex items-center gap-2 mt-1">
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded border ${statusBadge[r.status] || statusBadge.idle}`}
          >
            {r.status}
          </span>
          {r.is_active && <span className="text-[10px] text-amber-400">● live</span>}
          <StepDots run={r} />
        </div>
      </div>
      <button
        onClick={e => handleDeleteClick(e, r.id)}
        className={`text-xs px-1.5 py-0.5 rounded transition ${
          pendingDelete === r.id
            ? 'opacity-100 bg-red-500/20 text-red-300 border border-red-500/40'
            : 'opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-red-400'
        }`}
        title={pendingDelete === r.id ? 'Click again to confirm' : 'Delete'}
      >
        {pendingDelete === r.id ? '✓ delete?' : '✕'}
      </button>
    </div>
  );

  const order: Array<'Today' | 'Yesterday' | 'Earlier'> = ['Today', 'Yesterday', 'Earlier'];
  const nonEmptyGroups = order.filter(k => grouped[k].length > 0);

  return (
    <aside className="w-72 shrink-0 border-r border-zinc-800 flex flex-col">
      <div className="p-4 border-b border-zinc-800 space-y-2">
        <button
          onClick={onNew}
          className={`w-full px-3 py-2 rounded-md text-sm font-medium border flex items-center gap-2 ${
            isLanding && mode === 'auto'
              ? 'bg-emerald-500 border-emerald-400 text-zinc-950'
              : 'bg-zinc-800 border-zinc-700 hover:bg-zinc-700 text-zinc-100 border-l-2 border-l-emerald-500/70'
          }`}
        >
          <span aria-hidden className="text-base leading-none">⚡</span>
          <span className="flex-1 text-left">+ Auto run</span>
          <span className={`text-[10px] ${isLanding && mode === 'auto' ? 'text-zinc-900/70' : 'text-zinc-500'}`}>
            full pipeline
          </span>
        </button>
        <button
          onClick={onManual}
          className={`w-full px-3 py-2 rounded-md text-sm font-medium border flex items-center gap-2 ${
            isLanding && mode === 'manual'
              ? 'bg-amber-500 border-amber-400 text-zinc-950'
              : 'bg-zinc-800 border-zinc-700 hover:bg-zinc-700 text-zinc-100 border-dashed border-amber-500/60'
          }`}
          title="Generate scripts only, then you trigger each image/clip/merge/upload manually"
        >
          <span aria-hidden className="text-base leading-none">✋</span>
          <span className="flex-1 text-left">+ Manual run</span>
          <span className={`text-[10px] ${isLanding && mode === 'manual' ? 'text-zinc-900/70' : 'text-zinc-500'}`}>
            step by step
          </span>
        </button>
        <button
          onClick={onSettings}
          className={`w-full px-3 py-1.5 rounded-md text-sm font-medium border flex items-center gap-2 ${
            isLanding && mode === 'settings'
              ? 'bg-zinc-700 border-zinc-600 text-zinc-100'
              : 'bg-zinc-900 border-zinc-800 hover:bg-zinc-800 text-zinc-300'
          }`}
          title="API keys, OAuth, Grok session"
        >
          <span aria-hidden className="text-sm leading-none">⚙</span>
          <span className="flex-1 text-left">Settings</span>
          <span className={`text-[10px] ${isLanding && mode === 'settings' ? 'text-zinc-300' : 'text-zinc-500'}`}>
            keys + auth
          </span>
        </button>
      </div>

      {showSearch && (
        <div className="px-3 pt-3 pb-2 border-b border-zinc-800">
          <div className="relative">
            <span className="absolute left-2 top-1/2 -translate-y-1/2 text-xs text-zinc-500 pointer-events-none">⌕</span>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Filter runs…"
              className="w-full pl-6 pr-6 py-1.5 bg-zinc-950 border border-zinc-800 rounded text-xs text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-emerald-500"
            />
            {query && (
              <button
                onClick={() => setQuery('')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[10px] text-zinc-500 hover:text-zinc-300 px-1"
                title="Clear"
              >
                ✕
              </button>
            )}
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {runs.length === 0 ? (
          <div className="p-4 text-sm text-zinc-500">No runs yet.</div>
        ) : filtered.length === 0 ? (
          <div className="p-4 text-sm text-zinc-500">No runs match “{query}”.</div>
        ) : (
          nonEmptyGroups.map(label => (
            <div key={label}>
              <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider text-zinc-500 bg-zinc-950/60 sticky top-0">
                {label} <span className="text-zinc-600">· {grouped[label].length}</span>
              </div>
              {grouped[label].map(renderRun)}
            </div>
          ))
        )}
      </div>

      <div className="p-3 border-t border-zinc-800 flex items-center gap-2 text-[10px] text-zinc-500">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-500/60 animate-ping" />
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
        </span>
        <span className="flex-1">
          {counts.live > 0
            ? <><span className="text-amber-400">{counts.live} live</span> · {counts.done}/{counts.total} done</>
            : <>{counts.total} {counts.total === 1 ? 'run' : 'runs'} · {counts.done} done</>}
        </span>
        <span className="text-zinc-600">syncs every 5s</span>
      </div>
      <div className="px-3 py-2 border-t border-zinc-800 flex items-center justify-center gap-3 text-[10px] text-zinc-500">
        <a
          href="https://github.com/hemangjoshi37a/object-talk-pipeline"
          target="_blank"
          rel="noreferrer"
          className="hover:text-zinc-200 transition flex items-center gap-1"
          title="View source on GitHub"
        >
          <span aria-hidden>★</span>
          <span>GitHub</span>
        </a>
        <span className="text-zinc-700">·</span>
        <a
          href="https://hjlabs.in"
          target="_blank"
          rel="noreferrer"
          className="hover:text-emerald-400 transition flex items-center gap-1"
          title="hjLabs — parent company"
        >
          <span aria-hidden>🌐</span>
          <span>hjLabs.in</span>
        </a>
      </div>
    </aside>
  );
}
