import { useEffect, useState } from 'react';
import { api, type Run, type RunSettings } from '../api';

/**
 * Per-run settings panel — collapsible. Shows what was used to start the run
 * and lets the user edit any field before hitting Retry. PUT /api/runs/<id>/settings
 * just persists; it does NOT re-run the pipeline.
 */
export function RunSettingsPanel({ run, onUpdated }: {
  run: Run;
  onUpdated?: (r: Run) => void;
}) {
  const initial = run.settings;
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<RunSettings | null>(initial || null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (initial) setDraft(initial);
  }, [initial]);

  if (!initial || !draft) return null;

  const wpsDefault = Math.max(10, draft.clip_duration_s * 3 - 5);
  const effectiveMax = draft.max_words ?? wpsDefault;

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setErr(null);
    try {
      const updated = await api.updateRunSettings(run.id, draft);
      onUpdated?.(updated);
      setEditing(false);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    setDraft(initial);
    setEditing(false);
    setErr(null);
  };

  const Pill = ({ children, accent = 'zinc' }: { children: React.ReactNode; accent?: 'zinc' | 'emerald' | 'amber' }) => (
    <span className={
      'text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded-full border font-medium ' +
      (accent === 'emerald' ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/40' :
       accent === 'amber'   ? 'bg-amber-500/10 text-amber-300 border-amber-500/40' :
                              'bg-zinc-800 text-zinc-400 border-zinc-700')
    }>{children}</span>
  );

  const Field = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
    <div className="grid grid-cols-[140px_1fr] gap-2 items-center py-1">
      <div>
        <div className="text-xs text-zinc-300">{label}</div>
        {hint && <div className="text-[10px] text-zinc-500">{hint}</div>}
      </div>
      <div>{children}</div>
    </div>
  );

  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/40">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full px-3 py-2 flex items-center justify-between text-left
                   text-xs uppercase tracking-wider text-zinc-400 hover:text-zinc-200"
      >
        <span className="flex items-center gap-2">
          <span>{open ? '▾' : '▸'}</span>
          <span>Run settings</span>
          {!open && (
            <span className="flex items-center gap-1 normal-case tracking-normal">
              <Pill accent="emerald">{initial.video_provider || 'default'}</Pill>
              {initial.video_provider === 'comfyui' && initial.comfyui_engine && (
                <Pill accent="emerald">{initial.comfyui_engine}</Pill>
              )}
              <Pill>{initial.clip_count} × {initial.clip_duration_s}s</Pill>
              <Pill>≤ {initial.max_words ?? Math.max(10, initial.clip_duration_s * 3 - 5)} words</Pill>
              {initial.skip_images && <Pill accent="amber">text-only</Pill>}
              {initial.manual_mode && <Pill accent="amber">manual</Pill>}
            </span>
          )}
        </span>
        {open && !editing && (
          <span
            role="button"
            onClick={(e) => { e.stopPropagation(); setEditing(true); }}
            className="text-[11px] normal-case tracking-normal px-2 py-0.5 rounded border
                       border-zinc-700 text-zinc-300 hover:border-emerald-500 hover:text-emerald-300 cursor-pointer"
          >
            ✎ Edit
          </span>
        )}
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-zinc-800 space-y-1">
          <Field label="Video provider" hint="grok = X.AI; comfyui = local GPU">
            {editing ? (
              <div className="flex gap-1">
                {(['grok', 'comfyui'] as const).map(p => (
                  <button key={p} type="button"
                    onClick={() => setDraft({ ...draft, video_provider: p })}
                    className={
                      'px-2 py-1 rounded text-xs border transition ' +
                      (draft.video_provider === p
                        ? 'border-emerald-500/60 bg-emerald-500/10 text-zinc-100'
                        : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-500')
                    }>{p}</button>
                ))}
              </div>
            ) : (
              <span className="text-xs font-mono text-zinc-200">{initial.video_provider || '(default)'}</span>
            )}
          </Field>

          {(editing ? draft.video_provider : initial.video_provider) === 'comfyui' && (
            <Field label="ComfyUI engine">
              {editing ? (
                <div className="flex gap-1 flex-wrap">
                  {(['ltx', 'wan', 'wan_s2v'] as const).map(e => (
                    <button key={e} type="button"
                      onClick={() => setDraft({ ...draft, comfyui_engine: e })}
                      className={
                        'px-2 py-1 rounded text-xs border transition ' +
                        (draft.comfyui_engine === e
                          ? 'border-emerald-500/60 bg-emerald-500/10 text-zinc-100'
                          : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-500')
                      }>{e}</button>
                  ))}
                </div>
              ) : (
                <span className="text-xs font-mono text-zinc-200">{initial.comfyui_engine || '(default)'}</span>
              )}
            </Field>
          )}

          <Field label="Clip count" hint="1–20">
            {editing ? (
              <input type="number" min={1} max={20} value={draft.clip_count}
                onChange={e => setDraft({ ...draft, clip_count: Math.max(1, Math.min(20, parseInt(e.target.value) || 1)) })}
                className="w-20 px-2 py-1 bg-zinc-900 border border-zinc-700 rounded text-xs font-mono text-zinc-200"/>
            ) : (
              <span className="text-xs font-mono text-zinc-200">{initial.clip_count}</span>
            )}
          </Field>

          <Field label="Duration / clip" hint="5–30 s">
            {editing ? (
              <input type="number" min={5} max={30} value={draft.clip_duration_s}
                onChange={e => setDraft({ ...draft, clip_duration_s: Math.max(5, Math.min(30, parseInt(e.target.value) || 5)) })}
                className="w-20 px-2 py-1 bg-zinc-900 border border-zinc-700 rounded text-xs font-mono text-zinc-200"/>
            ) : (
              <span className="text-xs font-mono text-zinc-200">{initial.clip_duration_s}s</span>
            )}
          </Field>

          <Field label="Max words / script" hint={`default ${wpsDefault} (= dur × 3 − 5)`}>
            {editing ? (
              <div className="flex items-center gap-1">
                <input type="number" min={4} max={120} value={draft.max_words ?? ''}
                  placeholder={String(wpsDefault)}
                  onChange={e => {
                    const v = e.target.value.trim();
                    if (v === '') return setDraft({ ...draft, max_words: null });
                    const n = parseInt(v);
                    if (Number.isFinite(n)) setDraft({ ...draft, max_words: Math.max(4, Math.min(120, n)) });
                  }}
                  className="w-20 px-2 py-1 bg-zinc-900 border border-zinc-700 rounded text-xs font-mono text-zinc-200"/>
                {draft.max_words !== null && (
                  <button type="button" onClick={() => setDraft({ ...draft, max_words: null })}
                    className="text-[10px] px-1.5 py-1 rounded border border-zinc-700 text-zinc-400 hover:text-zinc-200">auto</button>
                )}
              </div>
            ) : (
              <span className="text-xs font-mono text-zinc-200">
                {initial.max_words ?? `${wpsDefault} (auto)`}
                <span className="text-zinc-500"> · {(effectiveMax / draft.clip_duration_s).toFixed(1)} wps</span>
              </span>
            )}
          </Field>

          <Field label="Privacy">
            {editing ? (
              <select value={draft.privacy}
                onChange={e => setDraft({ ...draft, privacy: e.target.value as RunSettings['privacy'] })}
                className="px-2 py-1 bg-zinc-900 border border-zinc-700 rounded text-xs text-zinc-200">
                <option value="public">public</option>
                <option value="unlisted">unlisted</option>
                <option value="private">private</option>
              </select>
            ) : (
              <span className="text-xs font-mono text-zinc-200">{initial.privacy}</span>
            )}
          </Field>

          <Field label="Flags">
            {editing ? (
              <div className="flex gap-3 flex-wrap text-xs text-zinc-300">
                <label className="flex items-center gap-1"><input type="checkbox"
                  checked={draft.skip_images} onChange={e => setDraft({ ...draft, skip_images: e.target.checked })}/> skip images</label>
                <label className="flex items-center gap-1"><input type="checkbox"
                  checked={draft.skip_upload} onChange={e => setDraft({ ...draft, skip_upload: e.target.checked })}/> skip upload</label>
                <label className="flex items-center gap-1"><input type="checkbox"
                  checked={draft.parallel} onChange={e => setDraft({ ...draft, parallel: e.target.checked })}/> parallel</label>
                <label className="flex items-center gap-1"><input type="checkbox"
                  checked={draft.headless} onChange={e => setDraft({ ...draft, headless: e.target.checked })}/> headless</label>
              </div>
            ) : (
              <div className="flex gap-1 flex-wrap">
                {initial.skip_images && <Pill accent="amber">skip-images</Pill>}
                {initial.skip_upload && <Pill accent="amber">skip-upload</Pill>}
                {initial.parallel && <Pill>parallel</Pill>}
                {initial.headless && <Pill>headless</Pill>}
                {!initial.skip_images && !initial.skip_upload && !initial.parallel && !initial.headless && (
                  <span className="text-xs text-zinc-500 italic">(none)</span>
                )}
              </div>
            )}
          </Field>

          {editing && (
            <div className="flex items-center gap-2 pt-2 mt-2 border-t border-zinc-800">
              <button type="button" onClick={save} disabled={saving}
                className="px-3 py-1.5 rounded-md bg-emerald-500/20 text-emerald-200 border border-emerald-500/40
                           hover:bg-emerald-500/30 text-xs font-medium disabled:opacity-50">
                {saving ? 'Saving…' : 'Save settings'}
              </button>
              <button type="button" onClick={cancel} disabled={saving}
                className="px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300
                           hover:border-zinc-500 text-xs">
                Cancel
              </button>
              <span className="text-[11px] text-zinc-500">
                Changes apply on next Retry — they don't re-run automatically.
              </span>
              {err && <span className="text-[11px] text-red-300">{err}</span>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
