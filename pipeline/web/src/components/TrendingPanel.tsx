import { useEffect, useState } from 'react';
import { api } from '../api';

interface TrendingItem {
  subject: string;
  category: string;
  reason: string;
}

const CATEGORIES = [
  'any', 'food', 'health', 'fitness', 'tech', 'lifestyle',
  'home', 'vehicle', 'finance', 'festival', 'fashion',
] as const;
type Category = (typeof CATEGORIES)[number];

const CAT_COLOR: Record<string, string> = {
  food: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  health: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  fitness: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  tech: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  lifestyle: 'bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30',
  home: 'bg-zinc-700/40 text-zinc-200 border-zinc-600/50',
  vehicle: 'bg-orange-500/15 text-orange-300 border-orange-500/30',
  finance: 'bg-yellow-500/15 text-yellow-300 border-yellow-500/30',
  festival: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
  fashion: 'bg-pink-500/15 text-pink-300 border-pink-500/30',
  default: 'bg-zinc-800 text-zinc-400 border-zinc-700',
};

export function TrendingPanel({
  onApply, completedSubjects,
}: {
  onApply: (subject: string) => void;
  completedSubjects: Set<string>;
}) {
  const [items, setItems] = useState<TrendingItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<Category>('any');
  const [cached, setCached] = useState(false);
  const [ageS, setAgeS] = useState(0);
  const [flashId, setFlashId] = useState<number | null>(null);

  const fetchTrending = async (refresh: boolean = false) => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.getTrending('IN', category, refresh);
      setItems(r.trending);
      setCached(r.cached);
      setAgeS(r.age_s);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchTrending(false); }, [category]);

  const slugOf = (s: string) => s.toLowerCase().split(/\s+/).join('-');
  const handleApply = (item: TrendingItem, i: number) => {
    onApply(item.subject);
    setFlashId(i);
    setTimeout(() => setFlashId(prev => (prev === i ? null : prev)), 900);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const ageLabel =
    !cached ? 'fresh' :
    ageS < 60 ? `${ageS}s ago` :
    ageS < 3600 ? `${Math.floor(ageS / 60)}m ago` :
    `${Math.floor(ageS / 3600)}h ago`;

  return (
    <div className="w-full max-w-xl mx-auto mt-6 border border-zinc-800 rounded-md overflow-hidden">
      <div className="px-4 py-3 border-b border-zinc-800 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-medium flex items-center gap-2">
              <span className="text-rose-400">⚡</span>
              Trending now
              <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded
                               bg-rose-500/10 text-rose-300 border border-rose-500/30 font-medium">
                IN
              </span>
            </div>
            <div className="text-[11px] text-zinc-500">
              Culturally relevant subjects right now — Gemini-curated, refreshed hourly.
            </div>
          </div>
          <button
            onClick={() => fetchTrending(true)}
            disabled={loading}
            className="text-[11px] px-2.5 py-1 rounded border border-zinc-700 bg-zinc-800
                       text-zinc-300 hover:bg-zinc-700 disabled:opacity-40"
            title="Force refresh (bypasses 1h cache)"
          >
            {loading ? '◌' : '↻'} {ageLabel}
          </button>
        </div>

        {/* Category chips */}
        <div className="flex flex-wrap gap-1.5">
          {CATEGORIES.map(c => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border transition ${
                category === c
                  ? 'bg-rose-500/20 text-rose-200 border-rose-500/50'
                  : 'bg-zinc-900 text-zinc-500 border-zinc-800 hover:text-zinc-300 hover:border-zinc-700'
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="px-4 py-2 text-xs text-red-400 border-b border-zinc-800">{error}</div>
      )}

      <div className="max-h-[40vh] overflow-y-auto divide-y divide-zinc-900">
        {loading && items.length === 0 ? (
          <div className="px-4 py-6 text-center text-zinc-500 text-sm italic">
            Generating trending list…
          </div>
        ) : items.length === 0 ? (
          <div className="px-4 py-6 text-center text-zinc-500 text-sm italic">
            No trending items yet. Try refresh.
          </div>
        ) : (
          items.map((it, i) => {
            const isDone = completedSubjects.has(slugOf(it.subject));
            const catCls = CAT_COLOR[it.category] || CAT_COLOR.default;
            const flashing = flashId === i;
            return (
              <div
                key={i}
                className={`group px-3 py-2 flex items-start gap-2 transition ${
                  flashing ? 'bg-emerald-500/10' : 'hover:bg-zinc-900/40'
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded border ${catCls}`}>
                      {it.category}
                    </span>
                    <span className={`text-sm font-medium ${isDone ? 'text-zinc-500 line-through' : 'text-zinc-100'}`}>
                      {it.subject}
                    </span>
                    {isDone && (
                      <span className="text-[9px] text-emerald-400/80">✓ has run</span>
                    )}
                  </div>
                  {it.reason && (
                    <div className="text-[11px] text-zinc-500 mt-0.5 leading-snug">{it.reason}</div>
                  )}
                </div>
                <button
                  onClick={() => handleApply(it, i)}
                  className="text-[10px] px-2 py-1 rounded border border-emerald-500/40
                             bg-emerald-500/10 text-emerald-300 opacity-60 group-hover:opacity-100
                             hover:bg-emerald-500/20 hover:border-emerald-500/60 shrink-0"
                  title="Push into the subject box"
                >
                  Apply ↑
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
