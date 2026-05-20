import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { SEED_IDEAS } from '../data/seedIdeas';

interface Idea {
  id: string;
  text: string;
  done: boolean;
  source: 'seed' | 'generated' | 'manual';
}

const LS_KEY = 'objtalk_ideas_v1';

function loadIdeas(): Idea[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {}
  return SEED_IDEAS.map((text, i) => ({
    id: `seed-${i}`,
    text,
    done: false,
    source: 'seed' as const,
  }));
}

function saveIdeas(ideas: Idea[]) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(ideas));
  } catch {}
}

function slugify(text: string): string {
  return text.toLowerCase().split(/\s+/).join('-');
}

const filterLabels: Record<string, string> = {
  all: 'All',
  todo: 'To do',
  done: 'Done',
};

// TODO(App.tsx): if you want the "✓ has run" label to actually jump to the
// matching run, pass an `onJumpToRun?: (slug: string) => void` prop here.

export function IdeasPanel({ onApply, completedSubjects }: {
  onApply: (subject: string) => void;
  completedSubjects: Set<string>;
}) {
  const [ideas, setIdeas] = useState<Idea[]>(() => loadIdeas());
  const [filter, setFilter] = useState<'all' | 'todo' | 'done'>('todo');
  const [theme, setTheme] = useState('');
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-mark ideas as done when a matching run exists in output/
  useEffect(() => {
    if (completedSubjects.size === 0) return;
    setIdeas(prev => {
      let changed = false;
      const next = prev.map(i => {
        const isDone = completedSubjects.has(slugify(i.text));
        if (isDone && !i.done) {
          changed = true;
          return { ...i, done: true };
        }
        return i;
      });
      return changed ? next : prev;
    });
  }, [completedSubjects]);

  useEffect(() => { saveIdeas(ideas); }, [ideas]);

  const counts = useMemo(() => ({
    all: ideas.length,
    todo: ideas.filter(i => !i.done).length,
    done: ideas.filter(i => i.done).length,
  }), [ideas]);

  const visible = useMemo(() => {
    if (filter === 'todo') return ideas.filter(i => !i.done);
    if (filter === 'done') return ideas.filter(i => i.done);
    return ideas;
  }, [ideas, filter]);

  const toggle = (id: string) => {
    setIdeas(prev => prev.map(i => (i.id === id ? { ...i, done: !i.done } : i)));
  };

  const remove = (id: string) => {
    setIdeas(prev => prev.filter(i => i.id !== id));
  };

  const handleApply = (idea: Idea) => {
    onApply(idea.text);
    setFlashId(idea.id);
    window.setTimeout(() => {
      setFlashId(prev => (prev === idea.id ? null : prev));
    }, 900);
    // Scroll the list to top and then the page itself so the user sees the form.
    if (scrollRef.current) scrollRef.current.scrollTo({ top: 0, behavior: 'smooth' });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const generate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const { ideas: newOnes } = await api.generateIdeas(theme || undefined, 10);
      const existing = new Set(ideas.map(i => i.text.toLowerCase()));
      const fresh = newOnes
        .filter(t => !existing.has(t.toLowerCase()))
        .map((text, idx) => ({
          id: `gen-${Date.now()}-${idx}`,
          text,
          done: false,
          source: 'generated' as const,
        }));
      setIdeas(prev => [...fresh, ...prev]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  };

  const resetSeeds = () => {
    if (!confirm('Reset list to the 50 seed ideas? (Keeps your done marks for matching texts.)')) return;
    const doneTexts = new Set(ideas.filter(i => i.done).map(i => i.text.toLowerCase()));
    setIdeas(
      SEED_IDEAS.map((text, i) => ({
        id: `seed-${i}`,
        text,
        done: doneTexts.has(text.toLowerCase()),
        source: 'seed' as const,
      })),
    );
  };

  const donePct = counts.all === 0 ? 0 : Math.round((counts.done / counts.all) * 100);

  return (
    <div className="w-full max-w-xl mx-auto mt-8 border border-zinc-800 rounded-md overflow-hidden">
      {/* Header: title + mini stat cards */}
      <div className="px-4 py-3 border-b border-zinc-800 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-medium">Idea backlog</div>
            <div className="text-[11px] text-zinc-500">Pick one to drop into the subject box.</div>
          </div>
          <div className="text-[10px] text-zinc-500 tabular-nums">{donePct}% done</div>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded border border-zinc-800 bg-zinc-900/50 px-2 py-1.5">
            <div className="text-base font-semibold text-zinc-100 leading-tight tabular-nums">{counts.all}</div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-500">Total</div>
          </div>
          <div className="rounded border border-amber-500/20 bg-amber-500/5 px-2 py-1.5">
            <div className="text-base font-semibold text-amber-300 leading-tight tabular-nums">{counts.todo}</div>
            <div className="text-[10px] uppercase tracking-wider text-amber-500/70">To do</div>
          </div>
          <div className="rounded border border-emerald-500/20 bg-emerald-500/5 px-2 py-1.5">
            <div className="text-base font-semibold text-emerald-300 leading-tight tabular-nums">{counts.done}</div>
            <div className="text-[10px] uppercase tracking-wider text-emerald-500/70">Done</div>
          </div>
        </div>
        {/* Filter tabs with readable counts */}
        <div className="flex bg-zinc-900 rounded-md text-xs overflow-hidden border border-zinc-800">
          {(['all', 'todo', 'done'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`flex-1 px-3 py-1.5 flex items-center justify-center gap-2 transition ${
                filter === f ? 'bg-zinc-700 text-zinc-100' : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <span>{filterLabels[f]}</span>
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded-full tabular-nums ${
                  filter === f ? 'bg-zinc-900/70 text-zinc-200' : 'bg-zinc-800 text-zinc-500'
                }`}
              >
                {counts[f]}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Generator / reset row — wraps onto two rows on narrow widths */}
      <div className="px-4 py-3 border-b border-zinc-800 bg-zinc-900/40 flex flex-wrap items-center gap-2">
        <input
          value={theme}
          onChange={e => setTheme(e.target.value)}
          placeholder="Optional theme (e.g. 'monsoon')"
          className="flex-1 min-w-[180px] px-2 py-1.5 bg-zinc-950 border border-zinc-800 rounded text-xs focus:outline-none focus:border-emerald-500"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={generate}
            disabled={generating}
            className="px-3 py-1.5 text-xs rounded border border-emerald-500/50 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-40"
          >
            {generating ? 'Generating…' : '+10 ideas'}
          </button>
          <button
            onClick={resetSeeds}
            className="px-2 py-1.5 text-xs rounded border border-zinc-700 bg-zinc-800 text-zinc-400 hover:text-zinc-200"
            title="Reset to seed ideas"
            aria-label="Reset to seed ideas"
          >
            ⟳
          </button>
        </div>
      </div>

      {error && (
        <div className="px-4 py-2 text-xs text-red-400 border-b border-zinc-800">{error}</div>
      )}

      <div ref={scrollRef} className="max-h-[40vh] overflow-y-auto divide-y divide-zinc-900">
        {visible.length === 0 ? (
          <div className="px-4 py-6 text-center text-zinc-500 text-sm italic">
            {filter === 'done' ? 'No ideas marked done yet.' : 'Nothing here — try +10 ideas.'}
          </div>
        ) : (
          visible.map(idea => {
            const hasRun = completedSubjects.has(slugify(idea.text));
            const isFlashing = flashId === idea.id;
            return (
              <div
                key={idea.id}
                className={`group flex items-center gap-2 px-3 py-1.5 transition-colors duration-500 ${
                  isFlashing ? 'bg-emerald-500/20' : 'hover:bg-zinc-900/40'
                }`}
              >
                <button
                  onClick={() => toggle(idea.id)}
                  aria-label={idea.done ? 'mark as not done' : 'mark as done'}
                  className={`shrink-0 w-4 h-4 rounded border-2 flex items-center justify-center transition ${
                    idea.done
                      ? 'bg-emerald-500 border-emerald-500'
                      : 'border-zinc-600 hover:border-zinc-400'
                  }`}
                >
                  {idea.done && (
                    <svg viewBox="0 0 16 16" className="w-3 h-3 text-zinc-950" fill="none" stroke="currentColor" strokeWidth={3}>
                      <polyline points="3,8 7,12 13,4" />
                    </svg>
                  )}
                </button>
                <div
                  className={`flex-1 min-w-0 text-sm truncate ${idea.done ? 'text-zinc-500 line-through' : 'text-zinc-200'}`}
                  title={idea.text}
                >
                  {idea.text}
                  {idea.source === 'generated' && (
                    <span className="ml-1.5 text-[9px] text-amber-400/80">new</span>
                  )}
                  {idea.done && hasRun && (
                    <span
                      className="ml-1.5 text-[9px] text-emerald-400/80"
                      title="A run exists in output/ for this subject"
                    >
                      ✓ has run
                    </span>
                  )}
                </div>
                <button
                  onClick={() => handleApply(idea)}
                  className="shrink-0 text-[10px] px-2 py-0.5 rounded border border-emerald-500/30 text-emerald-300/70 bg-emerald-500/5 hover:bg-emerald-500/20 hover:text-emerald-200 hover:border-emerald-500/60 opacity-60 group-hover:opacity-100 transition"
                  title="Push into the subject box"
                >
                  Apply ↑
                </button>
                <button
                  onClick={() => remove(idea.id)}
                  className="shrink-0 text-zinc-600 hover:text-red-400 text-xs px-1 opacity-0 group-hover:opacity-100"
                  title="Remove"
                  aria-label="Remove idea"
                >
                  ✕
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
