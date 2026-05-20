import { useEffect, useState } from 'react';
import type { RunOptions } from '../api';

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

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!subject.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({
        subject: subject.trim(),
        privacy,
        headless,
        skip_upload: skipUpload,
        parallel,
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
                  — 5 images at once + multi-tab video gen. Faster (~7 min → ~3 min) but may stress Grok rate limits.
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
