import { useEffect, useState } from 'react';
import { api, type Run } from '../api';
import { ScriptsEditor } from './ScriptsEditor';

const TOTAL_CLIPS = 5; // TODO: thread through from backend / settings if this ever varies

function ProgressChip({
  done, total, label, unlocked,
}: {
  done: number;
  total: number;
  label: string;
  unlocked: boolean;
}) {
  const pct = Math.min(100, Math.round((done / total) * 100));
  return (
    <div
      className={`inline-flex items-center gap-2 px-2 py-1 rounded-full text-[10px] font-medium border
                  ${unlocked
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                    : 'border-zinc-700 bg-zinc-900 text-zinc-400'}`}
    >
      <span className="tabular-nums">
        {done}/{total}
      </span>
      <div className="w-12 h-1 rounded-full bg-zinc-800 overflow-hidden">
        <div
          className={`h-full transition-all duration-300 ${unlocked ? 'bg-emerald-500' : 'bg-amber-500/60'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="uppercase tracking-wider">{label}</span>
    </div>
  );
}

function PrivacyToggle({
  value, onChange, disabled,
}: {
  value: 'public' | 'unlisted' | 'private';
  onChange: (v: 'public' | 'unlisted' | 'private') => void;
  disabled: boolean;
}) {
  const opts: Array<{ v: 'public' | 'unlisted' | 'private'; label: string; icon: string }> = [
    { v: 'public', label: 'Public', icon: '🌐' },
    { v: 'unlisted', label: 'Unlisted', icon: '🔗' },
    { v: 'private', label: 'Private', icon: '🔒' },
  ];
  return (
    <div className="inline-flex rounded-md border border-zinc-700 bg-zinc-950 overflow-hidden text-[11px]">
      {opts.map(o => {
        const active = value === o.v;
        return (
          <button
            key={o.v}
            type="button"
            disabled={disabled}
            onClick={() => onChange(o.v)}
            className={`px-2 py-1 flex items-center gap-1 border-r border-zinc-800 last:border-r-0 transition
                        ${active
                          ? 'bg-red-500/15 text-red-200'
                          : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900'}
                        disabled:opacity-40 disabled:cursor-not-allowed`}
            title={`Set privacy to ${o.label}`}
          >
            <span className="opacity-80">{o.icon}</span>
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function BusyHint({ kind, startedAt }: { kind: 'merge' | 'upload'; startedAt: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 500);
    return () => clearInterval(t);
  }, [startedAt]);
  const eta = kind === 'merge' ? '~10–30s' : '~30–90s';
  return (
    <div className="mt-2 flex items-center gap-2 text-[11px] text-amber-300">
      <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
      <span className="tabular-nums">
        {kind === 'merge' ? 'Concatenating clips' : 'Uploading to YouTube'}… {elapsed}s
      </span>
      <span className="text-zinc-500">· ETA {eta}</span>
      <div className="flex-1 h-0.5 bg-zinc-800 rounded overflow-hidden">
        <div className="h-full w-1/3 bg-gradient-to-r from-transparent via-amber-400 to-transparent animate-[shimmer_1.5s_linear_infinite]"
             style={{ animation: 'shimmer 1.5s linear infinite' }} />
      </div>
    </div>
  );
}

export function ArtifactsPanel({ run }: { run: Run }) {
  const { artifacts } = run;
  const [busy, setBusy] = useState<'merge' | 'upload' | null>(null);
  const [busyStart, setBusyStart] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [privacy, setPrivacy] = useState<'public' | 'unlisted' | 'private'>('public');
  const [copied, setCopied] = useState(false);

  const clipsReady = artifacts.videos.length;
  const canMerge = clipsReady > 0;
  const canUpload = !!artifacts.merged;

  const doMerge = async () => {
    setBusy('merge');
    setBusyStart(Date.now());
    setError(null);
    try {
      await api.manualMerge(run.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const doUpload = async () => {
    setBusy('upload');
    setBusyStart(Date.now());
    setError(null);
    try {
      await api.manualUpload(run.id, privacy);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const copyYoutubeUrl = async () => {
    if (!run.youtube_url) return;
    try {
      await navigator.clipboard.writeText(run.youtube_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="flex flex-col h-full border border-zinc-800 rounded-md overflow-hidden">
      <div className="px-3 py-2 bg-zinc-900 border-b border-zinc-800 text-xs uppercase tracking-wider text-zinc-400 flex items-center justify-between">
        <span>Artifacts</span>
        <span className="text-[10px] normal-case text-zinc-500 font-mono">
          {clipsReady}/{TOTAL_CLIPS} clips · {artifacts.merged ? 'merged ✓' : 'no merge'} · {run.youtube_url ? 'uploaded ✓' : 'not uploaded'}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-5">
        {artifacts.scripts_json ? (
          <ScriptsEditor
            runId={run.id}
            artifacts={artifacts}
            activeStep={run.is_active ? run.current_step : null}
          />
        ) : (
          <div className="text-xs text-zinc-500 italic">scripts.json not generated yet…</div>
        )}

        {error && (
          <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded p-2 flex items-start gap-2">
            <span className="opacity-80">⚠</span>
            <span className="flex-1 break-all">{error}</span>
            <button
              onClick={() => setError(null)}
              className="text-red-400/70 hover:text-red-300"
              title="Dismiss"
            >
              ✕
            </button>
          </div>
        )}

        {/* ── Final actions header ─────────────────────────────────────── */}
        <div className="flex items-center gap-2 pt-2">
          <div className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">Final actions</div>
          <div className="flex-1 h-px bg-gradient-to-r from-zinc-800 via-zinc-800/50 to-transparent" />
        </div>

        {/* ── Merge action card ────────────────────────────────────────── */}
        <div
          className={`relative rounded-lg border bg-gradient-to-br from-zinc-900/70 to-zinc-900/30 p-4 transition
                      ${canMerge
                        ? 'border-emerald-500/30 shadow-[0_0_0_1px_rgba(16,185,129,0.04)]'
                        : 'border-zinc-800'}`}
        >
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-3 min-w-0">
              <div
                className={`w-9 h-9 rounded-lg flex items-center justify-center text-lg
                            ${canMerge ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30' : 'bg-zinc-800/60 text-zinc-500 border border-zinc-800'}`}
              >
                ⛓
              </div>
              <div className="min-w-0">
                <div className="text-sm font-semibold flex items-center gap-2">
                  Step 1 · Merge
                  {artifacts.merged && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                      ✓ done
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-zinc-500">
                  Concatenate clips into <span className="font-mono text-zinc-400">merge.mp4</span>.
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <ProgressChip
                done={clipsReady}
                total={TOTAL_CLIPS}
                label="clips"
                unlocked={canMerge}
              />
              <button
                onClick={doMerge}
                disabled={!canMerge || busy === 'merge' || run.is_active}
                title={!canMerge ? `Need at least one clip (you have ${clipsReady})` : run.is_active ? 'A run is active' : ''}
                className="px-4 py-1.5 text-sm font-medium rounded-md border border-emerald-500/50 bg-emerald-500/10 text-emerald-300
                           hover:bg-emerald-500/20 hover:border-emerald-400/70
                           disabled:opacity-40 disabled:cursor-not-allowed transition"
              >
                {busy === 'merge' ? '◌ Merging…' : artifacts.merged ? '↻ Re-merge' : '▶ Merge'}
              </button>
            </div>
          </div>

          {!canMerge && (
            <div className="mt-2 text-[11px] text-zinc-500 flex items-center gap-1">
              <span>🔒</span>
              <span>Merge unlocks when at least 1 clip is ready (you have {clipsReady}/{TOTAL_CLIPS}).</span>
            </div>
          )}

          {busy === 'merge' && <BusyHint kind="merge" startedAt={busyStart} />}

          {artifacts.merged && (
            <div className="mt-3 flex items-start gap-3">
              <video
                src={artifacts.merged}
                controls
                preload="metadata"
                className="w-1/3 max-w-[200px] aspect-[9/16] rounded-md border border-zinc-800 bg-black"
              />
              <div className="text-[11px] text-zinc-500 leading-relaxed">
                <div className="font-mono text-zinc-400 break-all">{artifacts.merged.split('/').pop()}</div>
                <div className="mt-1">Ready for upload.</div>
              </div>
            </div>
          )}
        </div>

        {/* ── Upload action card ───────────────────────────────────────── */}
        <div
          className={`relative rounded-lg border bg-gradient-to-br from-zinc-900/70 to-zinc-900/30 p-4 transition
                      ${canUpload
                        ? 'border-red-500/30 shadow-[0_0_0_1px_rgba(239,68,68,0.04)]'
                        : 'border-zinc-800'}`}
        >
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-3 min-w-0">
              <div
                className={`w-9 h-9 rounded-lg flex items-center justify-center text-lg
                            ${canUpload ? 'bg-red-500/15 text-red-300 border border-red-500/30' : 'bg-zinc-800/60 text-zinc-500 border border-zinc-800'}`}
              >
                ▲
              </div>
              <div className="min-w-0">
                <div className="text-sm font-semibold flex items-center gap-2">
                  Step 2 · Upload to YouTube
                  {run.youtube_url && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                      ✓ live
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-zinc-500">
                  Auto-generates title / description / tags via Gemini, then uploads <span className="font-mono text-zinc-400">merge.mp4</span>.
                </div>
              </div>
            </div>
            <button
              onClick={doUpload}
              disabled={!canUpload || busy === 'upload' || run.is_active}
              title={!canUpload ? 'Merge first to produce merge.mp4' : run.is_active ? 'A run is active' : ''}
              className="px-4 py-1.5 text-sm font-medium rounded-md border border-red-500/50 bg-red-500/10 text-red-300
                         hover:bg-red-500/20 hover:border-red-400/70
                         disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              {busy === 'upload' ? '◌ Uploading…' : run.youtube_url ? '↻ Re-upload' : '▲ Upload'}
            </button>
          </div>

          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <span className="text-[10px] uppercase tracking-wider text-zinc-500">Privacy</span>
            <PrivacyToggle
              value={privacy}
              onChange={setPrivacy}
              disabled={busy === 'upload' || run.is_active}
            />
          </div>

          {!canUpload && (
            <div className="mt-2 text-[11px] text-zinc-500 flex items-center gap-1">
              <span>🔒</span>
              <span>Upload unlocks when <span className="font-mono">merge.mp4</span> exists.</span>
            </div>
          )}

          {busy === 'upload' && <BusyHint kind="upload" startedAt={busyStart} />}

          {run.youtube_url && (
            <div className="mt-3 rounded-md border border-emerald-500/30 bg-gradient-to-br from-emerald-500/10 to-emerald-500/5 p-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-lg">🎉</span>
                <span className="text-sm font-semibold text-emerald-300">Live on YouTube</span>
                <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-200">
                  ✓ success
                </span>
              </div>
              <div className="flex items-center gap-2">
                <a
                  href={run.youtube_url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex-1 text-emerald-300 hover:text-emerald-200 hover:underline break-all text-sm font-mono px-2 py-1 rounded bg-zinc-950/60 border border-emerald-500/20"
                >
                  {run.youtube_url}
                </a>
                <button
                  onClick={copyYoutubeUrl}
                  className="px-2 py-1 text-xs rounded border border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800 hover:border-zinc-600 transition whitespace-nowrap"
                  title="Copy URL to clipboard"
                >
                  {copied ? '✓ Copied' : '⧉ Copy'}
                </button>
                <a
                  href={run.youtube_url}
                  target="_blank"
                  rel="noreferrer"
                  className="px-2 py-1 text-xs rounded border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 transition whitespace-nowrap"
                  title="Open in new tab"
                >
                  ↗ Open
                </a>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
