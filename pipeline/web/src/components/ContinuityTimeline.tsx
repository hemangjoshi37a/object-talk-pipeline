import { useState } from 'react';

interface ContinuityTimelineProps {
  runId: string;
  clipCount: number;
  artifacts: {
    starters: string[];
    videos: string[];
    last_frames: string[];
    product_images: string[];
  };
  approvals: {
    awaiting: number | null;
    approved: number[];
    rejected: number[];
  };
  reviewMode: 'auto' | 'per_clip';
  onApprove: (idx: number) => Promise<void>;
  onReject: (idx: number, reason?: string) => Promise<void>;
}

type ClipStatus = 'awaiting' | 'approved' | 'rejected' | 'running' | 'queued' | 'done';

function findArtifact(arr: string[], idx: number): string | null {
  const needle = `_${String(idx).padStart(2, '0')}`;
  return arr.find(s => s.includes(needle)) || null;
}

function clipStatus(
  idx: number,
  approvals: ContinuityTimelineProps['approvals'],
  hasVideo: boolean,
): ClipStatus {
  if (approvals.rejected.includes(idx)) return 'rejected';
  if (approvals.approved.includes(idx)) return 'approved';
  if (approvals.awaiting === idx) return 'awaiting';
  if (hasVideo) return 'done';
  // Heuristic: the lowest unfinished index after approvals is "running",
  // anything past it is "queued". Without explicit run state, treat awaiting
  // (none) + missing video + no approval as queued.
  const maxResolved = Math.max(
    approvals.approved.length ? Math.max(...approvals.approved) : 0,
    approvals.rejected.length ? Math.max(...approvals.rejected) : 0,
  );
  if (approvals.awaiting != null && idx < approvals.awaiting) return 'done';
  if (idx === maxResolved + 1) return 'running';
  return 'queued';
}

const STATUS_STYLE: Record<ClipStatus, { card: string; chip: string; label: string }> = {
  awaiting: {
    card: 'border-amber-500/50 shadow-[0_0_0_1px_rgba(245,158,11,0.35),0_0_18px_-2px_rgba(245,158,11,0.55)]',
    chip: 'bg-amber-500/15 text-amber-200 border-amber-400/60',
    label: 'Awaiting approval',
  },
  approved: {
    card: 'border-emerald-500/40',
    chip: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
    label: 'Approved',
  },
  rejected: {
    card: 'border-red-500/50',
    chip: 'bg-red-500/15 text-red-300 border-red-500/50',
    label: 'Rejected',
  },
  running: {
    card: 'border-amber-400/40',
    chip: 'bg-amber-500/15 text-amber-200 border-amber-400/60',
    label: 'Running',
  },
  queued: {
    card: 'border-zinc-800',
    chip: 'bg-zinc-800 text-zinc-400 border-zinc-700',
    label: 'Queued',
  },
  done: {
    card: 'border-zinc-700',
    chip: 'bg-zinc-800 text-zinc-300 border-zinc-700',
    label: 'Done',
  },
};

function StatusChip({ status }: { status: ClipStatus }) {
  const s = STATUS_STYLE[status];
  const showPulse = status === 'awaiting' || status === 'running';
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border font-semibold ${s.chip}`}
    >
      {showPulse && (
        <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      )}
      {s.label}
    </span>
  );
}

function Tile({
  src, kind, missingLabel, finalNote,
}: {
  src: string | null;
  kind: 'image' | 'video';
  missingLabel: string;
  finalNote?: string;
}) {
  if (finalNote) {
    return (
      <div className="relative w-full aspect-[9/16] rounded-md border border-dashed border-zinc-800 bg-zinc-900/30
                      flex items-center justify-center text-[10px] text-zinc-600 uppercase tracking-wider">
        {finalNote}
      </div>
    );
  }

  if (!src) {
    return (
      <div className="relative w-full aspect-[9/16] rounded-md border border-dashed border-zinc-700 bg-zinc-900/40
                      flex flex-col items-center justify-center gap-1 text-zinc-500 text-[10px] uppercase tracking-wider">
        <span className="absolute top-1 left-1 text-zinc-700 text-[10px] leading-none">┌</span>
        <span className="absolute top-1 right-1 text-zinc-700 text-[10px] leading-none">┐</span>
        <span className="absolute bottom-1 left-1 text-zinc-700 text-[10px] leading-none">└</span>
        <span className="absolute bottom-1 right-1 text-zinc-700 text-[10px] leading-none">┘</span>
        <div className="text-xl opacity-60">{kind === 'image' ? '🖼' : '🎬'}</div>
        <div>{missingLabel}</div>
      </div>
    );
  }

  if (kind === 'video') {
    return (
      <video
        src={src}
        controls
        preload="metadata"
        className="w-full aspect-[9/16] object-cover rounded-md border border-zinc-800 bg-black"
      />
    );
  }

  return (
    <a href={src} target="_blank" rel="noreferrer">
      <img
        src={src}
        alt=""
        className="w-full aspect-[9/16] object-cover rounded-md border border-zinc-800 hover:border-emerald-500/60 transition"
      />
    </a>
  );
}

function TileLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] uppercase text-zinc-500 tracking-wider">{children}</div>
  );
}

function ApprovalActions({
  idx,
  busy,
  onApprove,
  onReject,
}: {
  idx: number;
  busy: 'approve' | 'reject' | null;
  onApprove: () => void;
  onReject: (reason?: string) => void;
}) {
  const [showReject, setShowReject] = useState(false);
  const [reason, setReason] = useState('');

  if (showReject) {
    return (
      <div className="space-y-1.5">
        <textarea
          value={reason}
          onChange={e => setReason(e.target.value)}
          rows={2}
          placeholder={`Optional: why are you rejecting clip #${idx}?`}
          className="w-full bg-zinc-950 border border-zinc-800 rounded p-1.5 text-[11px] focus:border-red-500/60 focus:outline-none resize-y"
        />
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => onReject(reason.trim() || undefined)}
            disabled={busy === 'reject'}
            className="flex-1 text-[11px] px-2 py-1 rounded border border-red-500/50 bg-red-500/10 text-red-300
                       hover:bg-red-500/20 hover:border-red-400/70
                       disabled:opacity-40 disabled:cursor-not-allowed transition"
          >
            {busy === 'reject' ? 'Rejecting…' : '✗ Confirm reject'}
          </button>
          <button
            type="button"
            onClick={() => { setShowReject(false); setReason(''); }}
            disabled={busy === 'reject'}
            className="text-[11px] px-2 py-1 rounded border border-zinc-700 bg-zinc-800/70 text-zinc-300
                       hover:bg-zinc-700 hover:border-zinc-600 transition
                       disabled:opacity-40 disabled:cursor-not-allowed"
          >
            cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={onApprove}
        disabled={!!busy}
        className="flex-1 text-[11px] px-2 py-1 rounded border border-emerald-500/50 bg-emerald-500/10 text-emerald-300
                   hover:bg-emerald-500/20 hover:border-emerald-400/70
                   disabled:opacity-40 disabled:cursor-not-allowed transition"
      >
        {busy === 'approve' ? 'Approving…' : '✓ Approve'}
      </button>
      <button
        type="button"
        onClick={() => setShowReject(true)}
        disabled={!!busy}
        className="flex-1 text-[11px] px-2 py-1 rounded border border-red-500/50 bg-red-500/10 text-red-300
                   hover:bg-red-500/20 hover:border-red-400/70
                   disabled:opacity-40 disabled:cursor-not-allowed transition"
      >
        ✗ Reject
      </button>
    </div>
  );
}

function Connector({ active }: { active: boolean }) {
  return (
    <div
      className="shrink-0 flex flex-col items-center justify-center self-stretch px-1 select-none"
      aria-hidden
    >
      <div className="text-[10px] uppercase tracking-wider text-zinc-600 mb-1 whitespace-nowrap">
        last → starter
      </div>
      <div className="flex items-center">
        <div className={`h-0.5 w-6 ${active ? 'bg-emerald-500/60' : 'bg-zinc-800'}`} />
        <div className={`text-base leading-none -ml-0.5 ${active ? 'text-emerald-500/70' : 'text-zinc-700'}`}>
          ▸
        </div>
      </div>
    </div>
  );
}

export function ContinuityTimeline({
  runId: _runId,
  clipCount,
  artifacts,
  approvals,
  reviewMode,
  onApprove,
  onReject,
}: ContinuityTimelineProps) {
  const [busy, setBusy] = useState<Record<number, 'approve' | 'reject' | null>>({});

  const setRowBusy = (idx: number, v: 'approve' | 'reject' | null) =>
    setBusy(b => ({ ...b, [idx]: v }));

  const handleApprove = async (idx: number) => {
    setRowBusy(idx, 'approve');
    try {
      await onApprove(idx);
    } finally {
      setRowBusy(idx, null);
    }
  };

  const handleReject = async (idx: number, reason?: string) => {
    setRowBusy(idx, 'reject');
    try {
      await onReject(idx, reason);
    } finally {
      setRowBusy(idx, null);
    }
  };

  if (clipCount <= 0) {
    return (
      <div className="text-xs text-zinc-500 italic px-1 py-3">
        No clips planned yet — once the plan is generated, the continuity timeline will appear here.
      </div>
    );
  }

  const approvedCount = approvals.approved.length;
  const rejectedCount = approvals.rejected.length;
  const doneVideos = artifacts.videos.length;

  return (
    <div className="space-y-3">
      {/* Header strip */}
      <div className="flex items-center justify-between gap-3 px-1">
        <div className="flex items-center gap-3 min-w-0">
          <div className="text-xs uppercase text-zinc-400 tracking-wider font-medium">
            Continuity timeline <span className="text-zinc-600">({clipCount} clips)</span>
          </div>
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-500">
            <span className="px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-800">
              🎬 {doneVideos}/{clipCount}
            </span>
            <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-300">
              ✓ {approvedCount}
            </span>
            {rejectedCount > 0 && (
              <span className="px-1.5 py-0.5 rounded bg-red-500/10 border border-red-500/30 text-red-300">
                ✗ {rejectedCount}
              </span>
            )}
            <span
              className={`px-1.5 py-0.5 rounded border ${
                reviewMode === 'per_clip'
                  ? 'bg-amber-500/10 border-amber-500/40 text-amber-300'
                  : 'bg-zinc-900 border-zinc-800 text-zinc-400'
              }`}
            >
              {reviewMode === 'per_clip' ? 'review: per-clip' : 'review: auto'}
            </span>
          </div>
        </div>
      </div>

      {/* Horizontal strip */}
      <div className="overflow-x-auto snap-x snap-mandatory -mx-3 px-3 pb-2">
        <div className="flex items-stretch gap-0 min-w-min">
          {Array.from({ length: clipCount }, (_, i) => {
            const idx = i + 1;
            const isLast = idx === clipCount;
            const starter = findArtifact(artifacts.starters, idx);
            const video = findArtifact(artifacts.videos, idx);
            const lastFrame = findArtifact(artifacts.last_frames, idx);
            const status = clipStatus(idx, approvals, !!video);
            const stStyle = STATUS_STYLE[status];
            const showActions =
              reviewMode === 'per_clip' && approvals.awaiting === idx && !!video;
            const rowBusy = busy[idx] ?? null;

            // Connector is "active" when this clip is done and the next has a starter or beyond.
            const nextStarter = findArtifact(artifacts.starters, idx + 1);
            const connectorActive = !!lastFrame && (!!nextStarter || !!findArtifact(artifacts.videos, idx + 1));

            return (
              <div key={idx} className="flex items-stretch shrink-0">
                <div
                  className={`snap-start w-[220px] shrink-0 rounded-lg border bg-zinc-900/50 p-2.5 space-y-2 transition-colors ${stStyle.card}`}
                >
                  {/* Header */}
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[11px] font-mono text-zinc-400 leading-none">
                        #{String(idx).padStart(2, '0')}
                      </span>
                    </div>
                    <StatusChip status={status} />
                  </div>

                  {/* Starter image */}
                  <div className="space-y-1">
                    <TileLabel>Starter</TileLabel>
                    <Tile src={starter} kind="image" missingLabel="starter pending" />
                  </div>

                  {/* Video */}
                  <div className="space-y-1">
                    <TileLabel>Clip</TileLabel>
                    <Tile src={video} kind="video" missingLabel="clip pending" />
                  </div>

                  {/* Last frame */}
                  <div className="space-y-1">
                    <TileLabel>{isLast ? 'Last frame' : 'Last → next starter'}</TileLabel>
                    <Tile
                      src={lastFrame}
                      kind="image"
                      missingLabel="frame pending"
                      finalNote={isLast ? '— final clip —' : undefined}
                    />
                  </div>

                  {/* Action strip */}
                  {showActions ? (
                    <ApprovalActions
                      idx={idx}
                      busy={rowBusy}
                      onApprove={() => handleApprove(idx)}
                      onReject={r => handleReject(idx, r)}
                    />
                  ) : status === 'rejected' ? (
                    <div className="text-[10px] uppercase tracking-wider text-red-300/80 text-center">
                      regenerating…
                    </div>
                  ) : status === 'awaiting' && reviewMode === 'auto' ? (
                    <div className="text-[10px] uppercase tracking-wider text-amber-300/80 text-center">
                      auto-approving
                    </div>
                  ) : null}
                </div>

                {!isLast && <Connector active={connectorActive} />}
              </div>
            );
          })}
        </div>
      </div>

      <div className="text-[11px] text-zinc-500 italic px-1">
        Each card stacks starter image · generated clip · last frame. The arrow shows the last frame feeding the next clip's starter image.
      </div>
    </div>
  );
}
