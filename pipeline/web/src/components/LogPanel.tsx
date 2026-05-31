import { useEffect, useMemo, useRef, useState } from 'react';
import type { LogLine } from '../api';

function fmtTs(ts: number): string {
  // 1970-ish ts means the backend didn't stamp this event (legacy entry);
  // hide the timestamp entirely rather than showing nonsense.
  if (!ts || ts < 1_000_000_000) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-GB', { hour12: false }); // HH:MM:SS
}

/**
 * Classify a log line by content so we can color it.
 * Order matters: error patterns are checked before warning patterns.
 */
type LineKind = 'error' | 'warn' | 'success' | 'step' | 'info-strong' | 'info';

function classify(line: string): LineKind {
  const t = line.trimStart();

  // Errors: tracebacks, runtime errors, explicit failure markers
  if (/^(Traceback|RuntimeError|Error|TimeoutError|FileNotFoundError|TargetClosedError|HTTPError|Exception)/.test(t)) return 'error';
  if (/^\s*File ".*", line \d+/.test(line)) return 'error';
  if (/\braise\s+\w+/.test(t)) return 'error';
  if (/(✗|FAILED|failed|fatal|crash(ed)?|aborted|denied|forbidden|404\b|403\b|500\b|HTTP 4\d\d|HTTP 5\d\d)/.test(line) && !/retry/i.test(line)) return 'error';

  // Step headers from pipeline.py
  if (/^={3,}/.test(t)) return 'step';
  if (/^>>>\s*Step\s+\d/.test(t)) return 'step';
  if (/^---\s*Step\s+\d/.test(t)) return 'info-strong';

  // Warnings / retries / fallbacks
  if (/^\[attempt\s+\d+\]/.test(t)) return 'warn';
  if (/retry(ing)?\b/i.test(t)) return 'warn';
  if (/⚠|warning|fallback|skipped?|deprecated/i.test(t)) return 'warn';

  // Success markers
  if (/(✓|✅|saved\s|wrote\s|uploaded\s|complete[d]?|✔)/i.test(line)) return 'success';
  if (/^Uploaded:\s/.test(t)) return 'success';

  // [1/5] clip headers and "·" sub-lines
  if (/^\[\d+\/\d+\]\s/.test(t)) return 'info-strong';
  return 'info';
}

const KIND_STYLE: Record<LineKind, { text: string; border: string; bold?: boolean }> = {
  error:         { text: 'text-red-300',     border: 'border-red-500/60',     bold: true },
  warn:          { text: 'text-amber-300',   border: 'border-amber-500/60' },
  success:       { text: 'text-emerald-300', border: 'border-emerald-500/60' },
  step:          { text: 'text-cyan-300',    border: 'border-cyan-500/70',   bold: true },
  'info-strong': { text: 'text-zinc-100',    border: 'border-zinc-600' },
  info:          { text: 'text-zinc-400',    border: 'border-transparent' },
};

export function LogPanel({ logs, onClear }: { logs: LogLine[]; onClear?: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  // Filter chip: lets the user temporarily hide info-level chatter to focus on
  // problems. Pure UI state — doesn't touch the underlying log buffer.
  const [filter, setFilter] = useState<'all' | 'problems'>('all');
  // Brief visual confirmation when the user hits Copy.
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (followRef.current && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [logs, filter]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    followRef.current = atBottom;
  };

  // Classify once per line, then optionally filter.
  const classified = useMemo(
    () => logs.map((entry, i) => ({
      i, line: entry.text, ts: entry.ts, kind: classify(entry.text),
    })),
    [logs],
  );
  const visible = filter === 'all'
    ? classified
    : classified.filter(x => x.kind === 'error' || x.kind === 'warn');

  // Tally for the filter pill badge
  const counts = useMemo(() => {
    let err = 0, warn = 0;
    for (const c of classified) {
      if (c.kind === 'error') err++;
      else if (c.kind === 'warn') warn++;
    }
    return { err, warn };
  }, [classified]);

  const headerBtn =
    'text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border transition';

  return (
    <div className="flex flex-col h-full border border-zinc-800 rounded-md overflow-hidden">
      <div className="px-3 py-2 bg-zinc-900 border-b border-zinc-800 flex items-center gap-2">
        <span className="text-xs uppercase tracking-wider text-zinc-400">
          Logs <span className="text-zinc-500 font-mono normal-case">({logs.length})</span>
        </span>
        {counts.err > 0 && (
          <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded-full
                           bg-red-500/15 text-red-300 border border-red-500/40"
                title="error lines">
            {counts.err} err
          </span>
        )}
        {counts.warn > 0 && (
          <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded-full
                           bg-amber-500/15 text-amber-300 border border-amber-500/40"
                title="warning / retry lines">
            {counts.warn} warn
          </span>
        )}

        <div className="flex-1" />

        <button
          type="button"
          onClick={() => setFilter(f => f === 'all' ? 'problems' : 'all')}
          className={
            headerBtn + ' ' +
            (filter === 'problems'
              ? 'border-amber-500/60 bg-amber-500/15 text-amber-200'
              : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200')
          }
          title={filter === 'all' ? 'Show only errors + warnings' : 'Show everything'}
        >
          {filter === 'all' ? 'Filter ✕' : 'All ✓'}
        </button>

        <button
          type="button"
          onClick={async () => {
            // Always copy the FULL log buffer (not just the filtered view) —
            // pasting more context is better than less for debugging.
            const blob = logs.map(l => {
              const t = fmtTs(l.ts);
              return t ? `[${t}] ${l.text}` : l.text;
            }).join('\n');
            try {
              await navigator.clipboard.writeText(blob);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            } catch {
              // Clipboard API needs HTTPS or localhost; fall back to selecting
              // the log container's text so the user can ctrl-c manually.
              const el = ref.current;
              if (el) {
                const range = document.createRange();
                range.selectNodeContents(el);
                const sel = window.getSelection();
                sel?.removeAllRanges();
                sel?.addRange(range);
              }
            }
          }}
          disabled={logs.length === 0}
          className={
            headerBtn + ' ' +
            (logs.length === 0
              ? 'border-zinc-800 bg-zinc-900 text-zinc-700 cursor-not-allowed'
              : copied
                ? 'border-emerald-500/60 bg-emerald-500/15 text-emerald-200'
                : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-emerald-500/60 hover:text-emerald-300')
          }
          title="Copy the full log buffer to clipboard (all lines, not just filtered)"
        >
          {copied ? 'Copied ✓' : 'Copy'}
        </button>

        {onClear && (
          <button
            type="button"
            onClick={() => {
              followRef.current = true;
              onClear();
            }}
            disabled={logs.length === 0}
            className={
              headerBtn + ' ' +
              (logs.length === 0
                ? 'border-zinc-800 bg-zinc-900 text-zinc-700 cursor-not-allowed'
                : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-red-500/60 hover:text-red-300')
            }
            title="Clear the local log buffer (does not delete the persistent log file)"
          >
            Clear
          </button>
        )}
      </div>
      <div
        ref={ref}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto log-mono text-xs bg-zinc-950"
      >
        {logs.length === 0 ? (
          <div className="p-2 text-zinc-600 italic">waiting for output…</div>
        ) : visible.length === 0 ? (
          <div className="p-2 text-zinc-600 italic">
            no errors or warnings — toggle the filter to see everything.
          </div>
        ) : (
          visible.map(({ i, line, ts, kind }) => {
            const s = KIND_STYLE[kind];
            const t = fmtTs(ts);
            return (
              <div
                key={i}
                className={
                  'whitespace-pre-wrap pl-2 pr-2 py-px border-l-2 flex gap-2 ' +
                  s.border + ' ' + s.text + (s.bold ? ' font-semibold' : '')
                }
              >
                {t && (
                  <span className="text-zinc-600 select-none shrink-0 tabular-nums"
                        title={new Date(ts * 1000).toISOString()}>
                    {t}
                  </span>
                )}
                <span className="flex-1 min-w-0">{line}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
