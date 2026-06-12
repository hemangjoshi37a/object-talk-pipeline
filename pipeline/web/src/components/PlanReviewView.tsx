import { useEffect, useMemo, useState } from 'react';

interface PlanReviewViewProps {
  runId: string;
  plan: any | null;
  briefs: Record<number, any>;
  onSavePlan: (plan: any) => Promise<void>;
  onSaveBrief: (idx: number, brief: any) => Promise<void>;
}

type RoleKind = 'hook' | 'middle' | 'cta' | string;

const ROLE_STYLE: Record<string, string> = {
  hook: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
  middle: 'bg-zinc-800 text-zinc-300 border-zinc-700',
  cta: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
};

const VISUAL_STYLE_OPTIONS = [
  'photorealistic_product_film',
  'cinematic_documentary',
  'hand_drawn_animation',
  'stop_motion',
  'anime_painterly',
  'hyperreal_commercial',
  'pixar_3d_character',
  'noir_high_contrast',
];

function RoleBadge({ role }: { role: RoleKind }) {
  const key = (role || '').toLowerCase();
  const cls = ROLE_STYLE[key] || 'bg-zinc-800 text-zinc-400 border-zinc-700';
  return (
    <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border font-semibold ${cls}`}>
      {role || '—'}
    </span>
  );
}

function RefinedBadge() {
  return (
    <span
      className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border font-semibold
                 bg-emerald-500/10 text-emerald-300 border-emerald-500/40"
      title="This brief was refined using the previous clip's last frame as continuity context."
    >
      refined
    </span>
  );
}

function isRefinedBrief(brief: any): boolean {
  const notes = brief?.continuity_notes;
  if (typeof notes !== 'string' || !notes.trim()) return false;
  return notes.toLowerCase().includes('previous');
}

function StringOrObjectList({
  values, onChange, placeholder,
}: {
  values: any;
  onChange: (next: any[]) => void;
  placeholder?: string;
}) {
  const list: any[] = Array.isArray(values) ? values.slice() : [];
  const renderText = (v: any): string => {
    if (typeof v === 'string') return v;
    if (v && typeof v === 'object') {
      try { return JSON.stringify(v); } catch { return ''; }
    }
    return '';
  };
  const update = (i: number, s: string) => {
    const next = list.slice();
    const cur = next[i];
    if (cur && typeof cur === 'object') {
      try {
        const parsed = JSON.parse(s);
        next[i] = parsed;
      } catch {
        next[i] = s;
      }
    } else {
      next[i] = s;
    }
    onChange(next);
  };
  return (
    <div className="space-y-1">
      {list.map((v, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <span className="text-[10px] font-mono text-zinc-600 w-4 text-right">{i + 1}.</span>
          <TextInput value={renderText(v)} onChange={s => update(i, s)} placeholder={placeholder} />
          <button
            type="button"
            onClick={() => onChange(list.filter((_, j) => j !== i))}
            className="text-[10px] text-zinc-500 hover:text-red-400 px-1"
            title="Remove"
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...list, ''])}
        className="text-[10px] px-1.5 py-0.5 rounded border border-zinc-700 bg-zinc-800/60 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200 transition"
      >
        ＋ add
      </button>
    </div>
  );
}

function DirtyDot() {
  return (
    <span className="inline-flex items-center gap-1 text-[10px] text-amber-400 normal-case">
      <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
      unsaved
    </span>
  );
}

function FieldLabel({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div className="text-[10px] uppercase text-zinc-500 mb-0.5 tracking-wider flex items-center gap-1.5">
      <span>{children}</span>
      {hint && <span className="text-zinc-600 normal-case lowercase">— {hint}</span>}
    </div>
  );
}

function TextInput({
  value, onChange, placeholder, className = '', listId,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  listId?: string;
}) {
  return (
    <input
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      list={listId}
      className={`w-full bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-xs focus:border-emerald-500 focus:outline-none ${className}`}
    />
  );
}

function TextArea({
  value, onChange, placeholder, rows = 3, mono = false,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  rows?: number;
  mono?: boolean;
}) {
  return (
    <textarea
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      rows={rows}
      placeholder={placeholder}
      className={`w-full bg-zinc-950 border border-zinc-800 rounded p-2 text-xs focus:border-emerald-500 focus:outline-none resize-y ${mono ? 'log-mono' : ''}`}
    />
  );
}

function isColorString(s: any): boolean {
  if (typeof s !== 'string') return false;
  return /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(s.trim());
}

function PaletteSwatches({
  palette, onChange,
}: {
  palette: any;
  onChange: (next: any) => void;
}) {
  const list: string[] = Array.isArray(palette) ? palette.slice() : [];
  const update = (i: number, v: string) => {
    const next = list.slice();
    next[i] = v;
    onChange(next);
  };
  const add = () => onChange([...list, '#000000']);
  const remove = (i: number) => onChange(list.filter((_, j) => j !== i));

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {list.map((c, i) => (
        <div
          key={i}
          className="group flex items-center gap-1 bg-zinc-950 border border-zinc-800 rounded pl-1 pr-1 py-0.5"
        >
          <span
            className="w-4 h-4 rounded border border-zinc-700"
            style={{ background: isColorString(c) ? c : 'transparent' }}
            title={c}
          />
          <input
            value={c}
            onChange={e => update(i, e.target.value)}
            className="w-20 bg-transparent text-[10px] font-mono text-zinc-300 focus:outline-none"
          />
          <button
            type="button"
            onClick={() => remove(i)}
            className="opacity-0 group-hover:opacity-100 transition text-[10px] text-zinc-500 hover:text-red-400"
            title="Remove"
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="text-[10px] px-1.5 py-0.5 rounded border border-zinc-700 bg-zinc-800/60 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200 transition"
      >
        ＋ swatch
      </button>
    </div>
  );
}

function StringList({
  values, onChange, placeholder,
}: {
  values: any;
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const list: string[] = Array.isArray(values) ? values.slice() : [];
  const update = (i: number, v: string) => {
    const next = list.slice();
    next[i] = v;
    onChange(next);
  };
  return (
    <div className="space-y-1">
      {list.map((v, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <span className="text-[10px] font-mono text-zinc-600 w-4 text-right">{i + 1}.</span>
          <TextInput value={v} onChange={s => update(i, s)} placeholder={placeholder} />
          <button
            type="button"
            onClick={() => onChange(list.filter((_, j) => j !== i))}
            className="text-[10px] text-zinc-500 hover:text-red-400 px-1"
            title="Remove"
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...list, ''])}
        className="text-[10px] px-1.5 py-0.5 rounded border border-zinc-700 bg-zinc-800/60 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200 transition"
      >
        ＋ add
      </button>
    </div>
  );
}

function stableStr(v: any): string {
  try {
    return JSON.stringify(v ?? null);
  } catch {
    return '';
  }
}

export function PlanReviewView({
  runId: _runId, plan, briefs, onSavePlan, onSaveBrief,
}: PlanReviewViewProps) {
  // Local editable copies — initialized from props, tracked dirty by deep compare.
  const [planDraft, setPlanDraft] = useState<any>(plan);
  const [planBaseline, setPlanBaseline] = useState<string>(stableStr(plan));
  const [savingPlan, setSavingPlan] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  const [briefDrafts, setBriefDrafts] = useState<Record<number, any>>(briefs);
  const [briefBaselines, setBriefBaselines] = useState<Record<number, string>>(() => {
    const out: Record<number, string> = {};
    for (const k of Object.keys(briefs || {})) {
      out[Number(k)] = stableStr((briefs as any)[k]);
    }
    return out;
  });
  const [savingBrief, setSavingBrief] = useState<Record<number, boolean>>({});
  const [briefError, setBriefError] = useState<Record<number, string | null>>({});
  const [openBrief, setOpenBrief] = useState<Record<number, boolean>>({});

  // Re-sync if parent reloads plan/briefs (e.g., after server-side regen).
  useEffect(() => {
    const next = stableStr(plan);
    if (next !== planBaseline) {
      setPlanDraft(plan);
      setPlanBaseline(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan]);

  useEffect(() => {
    const updatedDrafts = { ...briefDrafts };
    const updatedBaselines = { ...briefBaselines };
    let changed = false;
    for (const k of Object.keys(briefs || {})) {
      const idx = Number(k);
      const next = stableStr((briefs as any)[k]);
      if (next !== updatedBaselines[idx]) {
        updatedDrafts[idx] = (briefs as any)[k];
        updatedBaselines[idx] = next;
        changed = true;
      }
    }
    if (changed) {
      setBriefDrafts(updatedDrafts);
      setBriefBaselines(updatedBaselines);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [briefs]);

  const planDirty = useMemo(() => stableStr(planDraft) !== planBaseline, [planDraft, planBaseline]);

  const briefDirty = (idx: number) =>
    stableStr(briefDrafts[idx]) !== (briefBaselines[idx] ?? stableStr(null));

  const updatePlan = (patch: any) => {
    setPlanDraft((p: any) => ({ ...(p || {}), ...patch }));
  };

  const updateClip = (i: number, patch: any) => {
    setPlanDraft((p: any) => {
      const clips = Array.isArray(p?.clips) ? p.clips.slice() : [];
      clips[i] = { ...(clips[i] || {}), ...patch };
      return { ...(p || {}), clips };
    });
  };

  const updateBrief = (idx: number, patch: any) => {
    setBriefDrafts(prev => ({ ...prev, [idx]: { ...(prev[idx] || {}), ...patch } }));
  };

  const updateBriefNested = (idx: number, key: string, patch: any) => {
    setBriefDrafts(prev => {
      const cur = prev[idx] || {};
      return { ...prev, [idx]: { ...cur, [key]: { ...(cur[key] || {}), ...patch } } };
    });
  };

  const savePlan = async () => {
    if (!planDirty) return;
    setSavingPlan(true);
    setPlanError(null);
    try {
      await onSavePlan(planDraft);
      setPlanBaseline(stableStr(planDraft));
    } catch (e: any) {
      setPlanError(e?.message || String(e));
    } finally {
      setSavingPlan(false);
    }
  };

  const saveBrief = async (idx: number) => {
    if (!briefDirty(idx)) return;
    setSavingBrief(s => ({ ...s, [idx]: true }));
    setBriefError(s => ({ ...s, [idx]: null }));
    try {
      await onSaveBrief(idx, briefDrafts[idx]);
      setBriefBaselines(b => ({ ...b, [idx]: stableStr(briefDrafts[idx]) }));
    } catch (e: any) {
      setBriefError(s => ({ ...s, [idx]: e?.message || String(e) }));
    } finally {
      setSavingBrief(s => ({ ...s, [idx]: false }));
    }
  };

  if (!planDraft) {
    return (
      <div className="text-xs text-zinc-500 italic px-1 py-3">
        Plan not generated yet — waiting for the planner step to finish.
      </div>
    );
  }

  const globals = planDraft || {};
  const clips: any[] = Array.isArray(planDraft?.clips) ? planDraft.clips : [];

  return (
    <div className="space-y-4">
      {/* Sticky header */}
      <div
        className="flex items-center justify-between sticky top-0 z-20 -mx-3 px-3 py-2
                   bg-zinc-950/85 backdrop-blur-md border-b border-zinc-800/80
                   shadow-[0_4px_12px_-8px_rgba(0,0,0,0.6)]"
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="text-xs uppercase text-zinc-400 tracking-wider font-medium">
            Plan review <span className="text-zinc-600">({clips.length} clips)</span>
          </div>
          {planDirty && <DirtyDot />}
        </div>
        <button
          onClick={savePlan}
          disabled={!planDirty || savingPlan}
          className="text-xs px-3 py-1 rounded border border-emerald-500/50 bg-emerald-500/10 text-emerald-300
                     hover:bg-emerald-500/20 hover:border-emerald-400/70
                     disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          {savingPlan ? 'Saving…' : planDirty ? '✓ Save plan' : 'Saved'}
        </button>
      </div>

      {planError && <div className="text-xs text-red-400">{planError}</div>}

      {/* Global card */}
      <div className={`rounded-lg border bg-zinc-900/50 p-3 space-y-3 transition-colors
                       ${planDirty ? 'border-amber-500/40' : 'border-zinc-800 hover:border-zinc-700'}`}>
        <div className="flex items-center justify-between">
          <div className="text-[11px] uppercase text-zinc-400 tracking-wider font-medium">
            Global · narrative + look
          </div>
        </div>

        <datalist id="visual-style-options">
          {VISUAL_STYLE_OPTIONS.map(opt => <option key={opt} value={opt} />)}
        </datalist>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="md:col-span-2">
            <FieldLabel hint="brand mission / why this exists">Vision statement</FieldLabel>
            <TextArea
              value={globals.vision_statement || ''}
              onChange={v => updatePlan({ vision_statement: v })}
              rows={2}
              placeholder="The brand's vision / mission this video is in service of"
            />
          </div>
          <div>
            <FieldLabel hint="emotion the viewer should leave with">Feeling to evoke</FieldLabel>
            <TextInput
              value={globals.feeling_to_evoke || ''}
              onChange={v => updatePlan({ feeling_to_evoke: v })}
              placeholder="e.g. quiet pride, hopeful momentum, focused calm"
            />
          </div>
          <div>
            <FieldLabel hint="who the viewer sees themselves as">Audience self-image</FieldLabel>
            <TextInput
              value={globals.audience_self_image || ''}
              onChange={v => updatePlan({ audience_self_image: v })}
              placeholder="e.g. a maker who finishes things"
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel hint="what the brand silently promises the viewer">Narrative promise</FieldLabel>
            <TextInput
              value={globals.narrative_promise || ''}
              onChange={v => updatePlan({ narrative_promise: v })}
              placeholder="e.g. your mornings can feel calm again"
            />
          </div>
          <div>
            <FieldLabel hint="rendering register">Visual style</FieldLabel>
            <TextInput
              value={globals.visual_style || ''}
              onChange={v => updatePlan({ visual_style: v })}
              placeholder="e.g. photorealistic_product_film"
              listId="visual-style-options"
            />
          </div>
          <div>
            <FieldLabel hint="why this style fits">Visual style notes</FieldLabel>
            <TextArea
              value={globals.visual_style_notes || ''}
              onChange={v => updatePlan({ visual_style_notes: v })}
              rows={2}
              placeholder="Director's note on why this look serves the feeling"
            />
          </div>
          <div>
            <FieldLabel hint="one-line story spine">Narrative logline</FieldLabel>
            <TextArea
              value={globals.narrative_logline || ''}
              onChange={v => updatePlan({ narrative_logline: v })}
              rows={2}
              placeholder="A one-line description of the whole 50s story"
            />
          </div>
          <div>
            <FieldLabel hint="setting / environment">World</FieldLabel>
            <TextArea
              value={globals.world || ''}
              onChange={v => updatePlan({ world: v })}
              rows={2}
              placeholder="Where this video lives visually"
            />
          </div>
          <div>
            <FieldLabel>Lighting style</FieldLabel>
            <TextInput
              value={globals.lighting_style || ''}
              onChange={v => updatePlan({ lighting_style: v })}
              placeholder="e.g. golden hour, soft studio key, neon rim"
            />
          </div>
          <div>
            <FieldLabel>Music mood</FieldLabel>
            <TextInput
              value={globals.music_mood || ''}
              onChange={v => updatePlan({ music_mood: v })}
              placeholder="e.g. uplifting acoustic, deep house, cinematic"
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel>Voice profile</FieldLabel>
            <TextArea
              value={globals.voice_profile || ''}
              onChange={v => updatePlan({ voice_profile: v })}
              rows={2}
              placeholder="Voice character: gender, age range, tone, pace, language"
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel hint="brand color palette">Palette</FieldLabel>
            <PaletteSwatches
              palette={globals.palette}
              onChange={next => updatePlan({ palette: next })}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel hint="recurring characters across clips">Characters</FieldLabel>
            <StringOrObjectList
              values={globals.characters}
              onChange={next => updatePlan({ characters: next })}
              placeholder="Character description"
            />
          </div>
        </div>
      </div>

      {/* Clip cards row */}
      <div className="space-y-3">
        <div className="text-[11px] uppercase text-zinc-400 tracking-wider font-medium px-1">
          Clips ({clips.length})
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {clips.map((clip, i) => {
            const idx = i + 1;
            const dirty = briefDirty(idx);
            const open = !!openBrief[idx];
            const brief = briefDrafts[idx] || {};
            const dialogue = brief.dialogue || {};
            const camera = brief.camera || {};
            const lighting = (brief.lighting && typeof brief.lighting === 'object') ? brief.lighting : {};
            const lightingIsString = typeof brief.lighting === 'string';
            const refined = isRefinedBrief(brief);
            const cardDirty = dirty;

            return (
              <div
                key={i}
                className={`rounded-lg border bg-zinc-900/50 p-3 space-y-3 transition-colors
                            ${cardDirty ? 'border-amber-500/40' : 'border-zinc-800 hover:border-zinc-700'}`}
              >
                {/* Clip header */}
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-[11px] font-mono text-zinc-500 leading-none">#{idx}</span>
                    <RoleBadge role={clip?.role || 'middle'} />
                    {refined && <RefinedBadge />}
                    {dirty && <DirtyDot />}
                  </div>
                  <button
                    onClick={() => saveBrief(idx)}
                    disabled={!dirty || !!savingBrief[idx]}
                    className="text-[11px] px-2 py-0.5 rounded border border-emerald-500/50 bg-emerald-500/10 text-emerald-300
                               hover:bg-emerald-500/20 hover:border-emerald-400/70
                               disabled:opacity-40 disabled:cursor-not-allowed transition"
                    title={`Save brief #${idx}`}
                  >
                    {savingBrief[idx] ? 'Saving…' : dirty ? '✓ Save brief' : 'Saved'}
                  </button>
                </div>

                {briefError[idx] && <div className="text-[11px] text-red-400">{briefError[idx]}</div>}

                {/* Plan-level clip fields */}
                <div className="space-y-2">
                  <div>
                    <FieldLabel>Purpose</FieldLabel>
                    <TextArea
                      value={clip?.purpose || ''}
                      onChange={v => updateClip(i, { purpose: v })}
                      rows={2}
                      placeholder="Why this clip exists in the story"
                    />
                  </div>
                  <div>
                    <FieldLabel>Key moment</FieldLabel>
                    <TextInput
                      value={clip?.key_moment || ''}
                      onChange={v => updateClip(i, { key_moment: v })}
                      placeholder="The hero beat the viewer remembers"
                    />
                  </div>
                  <div>
                    <FieldLabel>Narrative beat</FieldLabel>
                    <TextInput
                      value={clip?.narrative_beat || ''}
                      onChange={v => updateClip(i, { narrative_beat: v })}
                      placeholder="Story function (setup, escalation, payoff, …)"
                    />
                  </div>
                  <div>
                    <FieldLabel hint="visual / motion bridge from clip N-1">Continuity with previous</FieldLabel>
                    <TextArea
                      value={clip?.continuity_with_previous || ''}
                      onChange={v => updateClip(i, { continuity_with_previous: v })}
                      rows={2}
                      placeholder="How this clip picks up from the prior one"
                    />
                  </div>
                  <div>
                    <FieldLabel>Voiceover hint</FieldLabel>
                    <TextArea
                      value={clip?.voiceover_hint || ''}
                      onChange={v => updateClip(i, { voiceover_hint: v })}
                      rows={2}
                      placeholder="What the voiceover should say or imply"
                    />
                  </div>
                </div>

                {/* Brief expand toggle */}
                <button
                  type="button"
                  onClick={() => setOpenBrief(o => ({ ...o, [idx]: !open }))}
                  className="w-full text-[11px] px-2 py-1 rounded border border-zinc-700 bg-zinc-800/60 text-zinc-300
                             hover:bg-zinc-700 hover:border-zinc-600 transition flex items-center justify-between"
                >
                  <span>{open ? '▾ Hide brief details' : '▸ Show brief details'}</span>
                  <span className="text-zinc-500 normal-case lowercase">brief_{String(idx).padStart(2, '0')}.json</span>
                </button>

                {open && (
                  <div className="space-y-2 border-t border-zinc-800 pt-3">
                    <div>
                      <FieldLabel>Scene</FieldLabel>
                      <TextArea
                        value={brief.scene || ''}
                        onChange={v => updateBrief(idx, { scene: v })}
                        rows={3}
                        placeholder="Full description of the scene"
                      />
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <div>
                        <FieldLabel>Characters</FieldLabel>
                        <StringOrObjectList
                          values={brief.characters}
                          onChange={v => updateBrief(idx, { characters: v })}
                          placeholder="Character description"
                        />
                      </div>
                      <div>
                        <FieldLabel>Props</FieldLabel>
                        <StringList
                          values={brief.props}
                          onChange={v => updateBrief(idx, { props: v })}
                          placeholder="Prop / object"
                        />
                      </div>
                    </div>

                    {lightingIsString ? (
                      <div>
                        <FieldLabel>Lighting</FieldLabel>
                        <TextArea
                          value={brief.lighting || ''}
                          onChange={v => updateBrief(idx, { lighting: v })}
                          rows={2}
                          placeholder="Lighting setup / mood"
                        />
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                        <div>
                          <FieldLabel>Lighting · key</FieldLabel>
                          <TextArea
                            value={lighting.key || ''}
                            onChange={v => updateBriefNested(idx, 'lighting', { key: v })}
                            rows={2}
                            placeholder="Key light direction / quality"
                          />
                        </div>
                        <div>
                          <FieldLabel>Lighting · fill</FieldLabel>
                          <TextArea
                            value={lighting.fill || ''}
                            onChange={v => updateBriefNested(idx, 'lighting', { fill: v })}
                            rows={2}
                            placeholder="Fill / bounce"
                          />
                        </div>
                        <div>
                          <FieldLabel>Lighting · mood</FieldLabel>
                          <TextArea
                            value={lighting.mood || ''}
                            onChange={v => updateBriefNested(idx, 'lighting', { mood: v })}
                            rows={2}
                            placeholder="Overall mood / feel"
                          />
                        </div>
                      </div>
                    )}

                    <div>
                      <FieldLabel>Action</FieldLabel>
                      <TextArea
                        value={brief.action || ''}
                        onChange={v => updateBrief(idx, { action: v })}
                        rows={3}
                        placeholder="What happens on screen, beat by beat"
                      />
                    </div>

                    <div>
                      <FieldLabel hint="hex swatches">Color palette</FieldLabel>
                      <PaletteSwatches
                        palette={brief.color_palette}
                        onChange={next => updateBrief(idx, { color_palette: next })}
                      />
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      <div>
                        <FieldLabel>Camera shot type</FieldLabel>
                        <TextInput
                          value={camera.shot_type || camera.shot || ''}
                          onChange={v => updateBriefNested(idx, 'camera', { shot_type: v })}
                          placeholder="e.g. ECU, medium, wide"
                        />
                      </div>
                      <div>
                        <FieldLabel>Camera angle</FieldLabel>
                        <TextInput
                          value={camera.angle || ''}
                          onChange={v => updateBriefNested(idx, 'camera', { angle: v })}
                          placeholder="e.g. low angle"
                        />
                      </div>
                      <div>
                        <FieldLabel>Camera movement</FieldLabel>
                        <TextInput
                          value={camera.movement || ''}
                          onChange={v => updateBriefNested(idx, 'camera', { movement: v })}
                          placeholder="e.g. slow push-in"
                        />
                      </div>
                      <div>
                        <FieldLabel>Lens (mm)</FieldLabel>
                        <TextInput
                          value={camera.lens_mm != null ? String(camera.lens_mm) : (camera.lens || '')}
                          onChange={v => {
                            const n = Number(v);
                            updateBriefNested(idx, 'camera', {
                              lens_mm: v.trim() === '' ? '' : (Number.isFinite(n) ? n : v),
                            });
                          }}
                          placeholder="e.g. 35"
                        />
                      </div>
                    </div>

                    <div>
                      <FieldLabel hint="actual spoken line">Dialogue</FieldLabel>
                      <TextArea
                        value={dialogue.text || ''}
                        onChange={v => updateBriefNested(idx, 'dialogue', { text: v })}
                        rows={3}
                        placeholder="The line the character speaks in this clip"
                      />
                    </div>

                    <div>
                      <FieldLabel hint="Nano Banana Pro prompt">Image prompt</FieldLabel>
                      <TextArea
                        value={brief.image_prompt || ''}
                        onChange={v => updateBrief(idx, { image_prompt: v })}
                        rows={4}
                        mono
                        placeholder="Starter-frame prompt for the image model"
                      />
                    </div>

                    <div>
                      <FieldLabel hint="Grok prompt">Video prompt</FieldLabel>
                      <TextArea
                        value={brief.video_prompt || ''}
                        onChange={v => updateBrief(idx, { video_prompt: v })}
                        rows={4}
                        mono
                        placeholder="Motion / action prompt for the video model"
                      />
                    </div>

                    {brief.continuity_notes && (
                      <div>
                        <FieldLabel hint="last-frame description used to seed clip N+1">Continuity notes</FieldLabel>
                        <TextArea
                          value={brief.continuity_notes || ''}
                          onChange={v => updateBrief(idx, { continuity_notes: v })}
                          rows={2}
                          placeholder="What the final frame should look like for the next clip"
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {clips.length === 0 && (
          <div className="text-xs text-zinc-500 italic px-1">
            Plan has no clips yet.
          </div>
        )}
      </div>

      <div className="text-[11px] text-zinc-500 italic">
        Tip: edit any field → the card border turns amber → click Save plan / Save brief to commit.
      </div>
    </div>
  );
}
