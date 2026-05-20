import { useState } from 'react';

const EXAMPLE_SUBJECTS = ['monsoon snacks', 'street food in Delhi', 'home remedies for cough'];

export function ManualRunForm({
  subject, onSubjectChange, onSubmit,
}: {
  subject: string;
  onSubjectChange: (s: string) => void;
  onSubmit: (subject: string) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!subject.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(subject.trim());
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

        {/* Subject form */}
        <form onSubmit={submit} className="space-y-2">
          <label className="block text-sm font-medium text-zinc-300">
            Subject <span className="text-zinc-600 font-normal">— what should the 5 scripts be about?</span>
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
