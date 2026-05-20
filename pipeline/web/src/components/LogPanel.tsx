import { useEffect, useRef } from 'react';

export function LogPanel({ logs }: { logs: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  useEffect(() => {
    if (followRef.current && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [logs]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    followRef.current = atBottom;
  };

  return (
    <div className="flex flex-col h-full border border-zinc-800 rounded-md overflow-hidden">
      <div className="px-3 py-2 bg-zinc-900 border-b border-zinc-800 text-xs uppercase tracking-wider text-zinc-400">
        Logs ({logs.length})
      </div>
      <div ref={ref} onScroll={onScroll} className="flex-1 overflow-y-auto log-mono text-xs p-2 bg-zinc-950">
        {logs.length === 0 ? (
          <div className="text-zinc-600 italic">waiting for output…</div>
        ) : (
          logs.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap text-zinc-300">
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
