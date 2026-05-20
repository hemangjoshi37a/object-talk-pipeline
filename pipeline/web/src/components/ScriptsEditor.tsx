import { useEffect, useState } from 'react';
import { api, type Artifacts, type Script, type ScriptsPayload } from '../api';

function findArtifact(arr: string[], idx: number): string | null {
  const needle = `_${String(idx).padStart(2, '0')}_`;
  return arr.find(s => s.includes(needle)) || null;
}

function StatusDot({
  label, state, hint,
}: {
  label: string;
  state: 'ok' | 'pending' | 'blocked';
  hint?: string;
}) {
  const cls =
    state === 'ok'
      ? 'bg-emerald-500/80 border-emerald-400 text-zinc-950'
      : state === 'pending'
        ? 'bg-amber-500/20 border-amber-500/60 text-amber-300'
        : 'bg-zinc-800 border-zinc-700 text-zinc-500';
  const glyph = state === 'ok' ? '✓' : state === 'pending' ? '…' : '·';
  return (
    <div
      title={hint || `${label}: ${state}`}
      className={`w-5 h-5 rounded-full border flex items-center justify-center text-[10px] font-bold ${cls}`}
    >
      {glyph}
    </div>
  );
}

function WordMeter({ count, max = 40 }: { count: number; max?: number }) {
  const pct = Math.min(100, Math.round((count / max) * 100));
  const over = count > max;
  const warn = count > max - 5;
  const color = over
    ? 'bg-red-500'
    : warn
      ? 'bg-amber-500'
      : 'bg-emerald-500';
  const textCls = over
    ? 'bg-red-500/20 text-red-300'
    : warn
      ? 'bg-amber-500/20 text-amber-300'
      : 'bg-emerald-500/20 text-emerald-300';
  return (
    <div
      className="flex items-center gap-1.5"
      title={`Hindi script word count — must be ≤${max} to fit in Grok's 10s window`}
    >
      <div className="w-16 h-1.5 rounded-full bg-zinc-800 overflow-hidden">
        <div
          className={`h-full ${color} transition-all duration-200`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-[10px] px-1.5 py-0.5 rounded tabular-nums ${textCls}`}>
        {count}/{max}w
      </span>
    </div>
  );
}

function MediaPlaceholder({
  kind, onGenerate, busy, blocked,
}: {
  kind: 'image' | 'video';
  onGenerate: () => void;
  busy: boolean;
  blocked?: string | null;
}) {
  return (
    <div
      className={`relative w-full aspect-[9/16] rounded-md border border-dashed bg-zinc-900/40
                 flex flex-col items-center justify-center gap-2 px-2 text-zinc-500 text-[10px] uppercase tracking-wider
                 ${busy ? 'border-amber-500/40 animate-pulse' : 'border-zinc-700'}`}
    >
      {/* ASCII corner frame */}
      <span className="absolute top-1 left-1 text-zinc-700 text-[10px] leading-none">┌</span>
      <span className="absolute top-1 right-1 text-zinc-700 text-[10px] leading-none">┐</span>
      <span className="absolute bottom-1 left-1 text-zinc-700 text-[10px] leading-none">└</span>
      <span className="absolute bottom-1 right-1 text-zinc-700 text-[10px] leading-none">┘</span>

      <div className="text-2xl opacity-60">{kind === 'image' ? '🖼' : '🎬'}</div>
      <div className="text-zinc-500">{kind} missing</div>
      {blocked ? (
        <span
          title={blocked}
          className="px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-400 text-[10px] normal-case"
        >
          ⛔ {blocked}
        </span>
      ) : (
        <button
          onClick={onGenerate}
          disabled={busy}
          className="px-2.5 py-1 rounded-md border border-emerald-500/50 bg-emerald-500/10 text-emerald-300
                     text-[10px] normal-case font-medium tracking-wide
                     hover:bg-emerald-500/20 hover:border-emerald-400/70
                     disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          {busy ? '◌ Generating…' : '＋ Generate'}
        </button>
      )}
    </div>
  );
}

function RegenButton({ onClick, busy }: { onClick: () => void; busy: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      title="Regenerate"
      className={`absolute top-1 right-1 w-6 h-6 rounded-full bg-zinc-900/80 border
                 text-zinc-300 hover:text-emerald-300 hover:border-emerald-500/60
                 transition flex items-center justify-center text-xs disabled:opacity-60
                 ${busy ? 'border-amber-500/60 text-amber-300 opacity-100' : 'border-zinc-700 opacity-0 group-hover:opacity-100'}`}
    >
      <span className={busy ? 'inline-block animate-spin' : ''}>↻</span>
    </button>
  );
}

export function ScriptsEditor({
  runId, artifacts,
}: {
  runId: string;
  artifacts: Artifacts;
  // activeStep kept as an optional prop for callers, but we deliberately don't
  // use it to gate per-row busy state — each Generate button is independent.
  activeStep?: string | null;
}) {
  const [data, setData] = useState<ScriptsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});

  const regen = async (kind: 'image' | 'video', idx: number) => {
    const key = `${kind}-${idx}`;
    // Mark this *one* row busy. Other rows are untouched.
    setBusy(b => ({ ...b, [key]: true }));
    setError(null);
    // Safety timeout — if the subprocess hangs or fails silently, drop the
    // busy state so the user isn't stuck. The artifacts-watching effect will
    // also clear it as soon as the file lands, whichever happens first.
    const timeoutMs = kind === 'image' ? 60_000 : 300_000;
    const timeoutId = window.setTimeout(() => {
      setBusy(b => {
        const n = { ...b };
        delete n[key];
        return n;
      });
    }, timeoutMs);
    try {
      if (kind === 'image') await api.regenImage(runId, idx);
      else await api.regenVideo(runId, idx);
      // Do NOT clear busy on API return — the subprocess is still working.
    } catch (e: any) {
      setError(`${kind} regen #${idx}: ${e.message}`);
      window.clearTimeout(timeoutId);
      setBusy(b => {
        const n = { ...b };
        delete n[key];
        return n;
      });
    }
  };

  // Clear per-row busy as soon as the corresponding artifact lands.
  // This is the source-of-truth that the underlying subprocess actually wrote a file.
  useEffect(() => {
    setBusy(prev => {
      const next: Record<string, boolean> = { ...prev };
      let changed = false;
      for (let i = 1; i <= 5; i++) {
        if (next[`image-${i}`] && findArtifact(artifacts.images, i)) {
          delete next[`image-${i}`];
          changed = true;
        }
        if (next[`video-${i}`] && findArtifact(artifacts.videos, i)) {
          delete next[`video-${i}`];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [artifacts.images, artifacts.videos]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getScripts(runId)
      .then(d => { if (!cancelled) { setData(d); setDirty(false); } })
      .catch(e => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId]);

  const update = (i: number, patch: Partial<Script>) => {
    if (!data) return;
    const next = { ...data, scripts: data.scripts.map((s, idx) => (idx === i ? { ...s, ...patch } : s)) };
    if (patch.hindi_script !== undefined) {
      next.scripts[i].word_count = patch.hindi_script.split(/\s+/).filter(Boolean).length;
    }
    setData(next);
    setDirty(true);
  };

  const save = async () => {
    if (!data) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await api.putScripts(runId, data);
      setData(saved);
      setDirty(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="text-xs text-zinc-500 italic">Loading scripts…</div>;
  if (error) return <div className="text-xs text-red-400">{error}</div>;
  if (!data) return null;

  const imageCount = data.scripts.filter((_, i) => findArtifact(artifacts.images, i + 1)).length;
  const videoCount = data.scripts.filter((_, i) => findArtifact(artifacts.videos, i + 1)).length;

  return (
    <div className="space-y-3">
      <div
        className="flex items-center justify-between sticky top-0 z-20 -mx-3 px-3 py-2
                   bg-zinc-950/85 backdrop-blur-md border-b border-zinc-800/80
                   shadow-[0_4px_12px_-8px_rgba(0,0,0,0.6)]"
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="text-xs uppercase text-zinc-400 tracking-wider font-medium">
            Scripts <span className="text-zinc-600">({data.scripts.length})</span>
          </div>
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-500">
            <span className="px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-800">
              🖼 {imageCount}/{data.scripts.length}
            </span>
            <span className="px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-800">
              🎬 {videoCount}/{data.scripts.length}
            </span>
          </div>
          {dirty && (
            <span className="text-[10px] text-amber-400 normal-case flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
              unsaved
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              if (!confirm('Regenerate ALL 5 scripts from scratch via Gemini? Your edits will be lost.')) return;
              try { await api.regenScripts(runId); } catch (e: any) { setError(e.message); }
            }}
            className="text-xs px-2 py-1 rounded border border-zinc-700 bg-zinc-800/70 text-zinc-300 hover:bg-zinc-700 hover:border-zinc-600 transition"
            title="Regenerate all 5 scripts via Gemini (overwrites current)"
          >
            ↻ All scripts
          </button>
          <button
            onClick={save}
            disabled={!dirty || saving}
            className="text-xs px-3 py-1 rounded border border-emerald-500/50 bg-emerald-500/10 text-emerald-300
                       hover:bg-emerald-500/20 hover:border-emerald-400/70
                       disabled:opacity-40 disabled:cursor-not-allowed transition"
          >
            {saving ? 'Saving…' : dirty ? '✓ Save' : 'Saved'}
          </button>
        </div>
      </div>

      {data.scripts.map((s, i) => {
        const idx = i + 1;
        const img = findArtifact(artifacts.images, idx);
        const vid = findArtifact(artifacts.videos, idx);
        // Per-row independent busy: set ONLY when *this* index was clicked.
        // Stays true until the actual file lands (see effect below).
        const imgBusy = !img && !!busy[`image-${idx}`];
        const vidBusy = !vid && !!busy[`video-${idx}`];
        const scriptOk = s.hindi_script.trim().length > 0 && s.word_count <= 40;
        return (
          <div
            key={i}
            className="rounded-lg border border-zinc-800 bg-zinc-900/50 hover:border-zinc-700 transition-colors p-3
                       grid grid-cols-[20px_1fr_minmax(160px,180px)_minmax(160px,180px)] gap-3 items-start"
          >
            {/* Status track */}
            <div className="flex flex-col items-center gap-1.5 pt-1">
              <span className="text-[11px] font-mono text-zinc-500 leading-none">{idx}</span>
              <div className="w-px flex-1 bg-zinc-800 my-0.5" />
              <StatusDot
                label="Script"
                state={scriptOk ? 'ok' : 'pending'}
                hint={`Script ${scriptOk ? 'ready' : 'incomplete'} (${s.word_count}/40 words)`}
              />
              <StatusDot
                label="Image"
                state={img ? 'ok' : imgBusy ? 'pending' : 'blocked'}
                hint={img ? 'Image ready' : imgBusy ? 'Image generating…' : 'Image not generated'}
              />
              <StatusDot
                label="Clip"
                state={vid ? 'ok' : vidBusy ? 'pending' : 'blocked'}
                hint={vid ? 'Clip ready' : vidBusy ? 'Clip generating…' : !img ? 'Needs image first' : 'Clip not generated'}
              />
            </div>

            {/* Script column */}
            <div className="space-y-2 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  value={s.object}
                  onChange={e => update(i, { object: e.target.value })}
                  className="flex-1 min-w-[120px] bg-transparent text-sm font-medium border-b border-zinc-800 focus:border-emerald-500 focus:outline-none px-1 py-0.5"
                  placeholder="object name"
                />
                <WordMeter count={s.word_count} />
              </div>
              <div>
                <div className="text-[10px] uppercase text-zinc-500 mb-0.5 tracking-wider">Hindi script</div>
                <textarea
                  value={s.hindi_script}
                  onChange={e => update(i, { hindi_script: e.target.value })}
                  rows={4}
                  className="w-full bg-zinc-950 border border-zinc-800 rounded p-2 text-sm focus:border-emerald-500 focus:outline-none resize-y"
                />
              </div>
              <div>
                <div className="text-[10px] uppercase text-zinc-500 mb-0.5 tracking-wider">Image prompt</div>
                <textarea
                  value={s.image_prompt}
                  onChange={e => update(i, { image_prompt: e.target.value })}
                  rows={5}
                  className="w-full bg-zinc-950 border border-zinc-800 rounded p-2 text-xs focus:border-emerald-500 focus:outline-none resize-y log-mono"
                />
              </div>
            </div>

            {/* Image column */}
            <div className="space-y-1">
              <div className="text-[10px] uppercase text-zinc-500 tracking-wider">Image</div>
              {img ? (
                <div className={`relative group ${imgBusy ? 'opacity-50' : ''}`}>
                  <a href={img} target="_blank" rel="noreferrer" title={s.object}>
                    <img
                      src={img}
                      alt={s.object}
                      className="w-full aspect-[9/16] object-cover rounded-md border border-zinc-800 hover:border-emerald-500/60 transition"
                    />
                  </a>
                  <RegenButton
                    onClick={() => regen('image', idx)}
                    busy={imgBusy}
                  />
                </div>
              ) : (
                <MediaPlaceholder
                  kind="image"
                  onGenerate={() => regen('image', idx)}
                  busy={imgBusy}
                />
              )}
              <div className="text-[10px] text-zinc-500 truncate" title={s.object}>
                {s.object || '—'}
              </div>
            </div>

            {/* Video column */}
            <div className="space-y-1">
              <div className="text-[10px] uppercase text-zinc-500 tracking-wider">Clip</div>
              {vid ? (
                <div className={`relative group ${vidBusy ? 'opacity-50' : ''}`}>
                  <video
                    src={vid}
                    controls
                    preload="metadata"
                    className="w-full aspect-[9/16] object-cover rounded-md border border-zinc-800 bg-black"
                    title={s.hindi_script}
                  />
                  <RegenButton
                    onClick={() => regen('video', idx)}
                    busy={vidBusy}
                  />
                </div>
              ) : (
                <MediaPlaceholder
                  kind="video"
                  onGenerate={() => regen('video', idx)}
                  busy={vidBusy}
                  blocked={!img ? 'Need image first' : null}
                />
              )}
              <div className="text-[10px] text-zinc-500 line-clamp-2 leading-snug" title={s.hindi_script}>
                {s.hindi_script ? s.hindi_script.slice(0, 60) + (s.hindi_script.length > 60 ? '…' : '') : '—'}
              </div>
            </div>
          </div>
        );
      })}

      <div className="text-[11px] text-zinc-500 italic">
        Tip: edit scripts → Save → click ↻ on any tile or "Generate" on placeholders to regenerate that part only.
      </div>
    </div>
  );
}
