import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type Run, type SseEvent, type LogLine } from './api';
import { Sidebar } from './components/Sidebar';
import { NewRunForm } from './components/NewRunForm';
import { ManualRunForm } from './components/ManualRunForm';
import { RunView } from './components/RunView';
import { IdeasPanel } from './components/IdeasPanel';
import { TrendingPanel } from './components/TrendingPanel';
import { Settings } from './components/Settings';
import { ProductBriefForm } from './components/ProductBriefForm';

// Hash-based router (no react-router dep). Single source of truth: location.hash.
// URL scheme — paste-able + refresh-safe:
//   #/                       → Object Talk run landing (default)
//   #/product                → Product Video landing
//   #/manual                 → Manual run landing
//   #/settings               → Settings page
//   #/run/<id>               → Run detail page
type LandingMode = 'product' | 'auto' | 'manual' | 'settings';

type Route =
  | { kind: 'auto' }
  | { kind: 'product' }
  | { kind: 'manual' }
  | { kind: 'settings' }
  | { kind: 'run'; id: string };

function parseHash(hash: string): Route {
  const h = hash.replace(/^#\/?/, '');
  if (!h) return { kind: 'auto' };
  if (h === 'product') return { kind: 'product' };
  if (h === 'manual') return { kind: 'manual' };
  if (h === 'settings') return { kind: 'settings' };
  const m = h.match(/^run\/([^/?]+)/);
  if (m) return { kind: 'run', id: decodeURIComponent(m[1]) };
  return { kind: 'auto' };
}

function routeToHash(r: Route): string {
  if (r.kind === 'auto') return '#/';
  if (r.kind === 'product') return '#/product';
  if (r.kind === 'manual') return '#/manual';
  if (r.kind === 'settings') return '#/settings';
  return `#/run/${encodeURIComponent(r.id)}`;
}

const TABS: { mode: LandingMode; label: string }[] = [
  { mode: 'product', label: 'Product Video' },
  { mode: 'auto', label: 'Object Talk' },
  { mode: 'manual', label: 'Manual' },
  { mode: 'settings', label: 'Settings' },
];

export default function App() {
  const [runs, setRuns] = useState<Run[]>([]);
  const initialRoute = parseHash(window.location.hash);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialRoute.kind === 'run' ? initialRoute.id : null,
  );
  const [currentRun, setCurrentRun] = useState<Run | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [pendingSubject, setPendingSubject] = useState('');
  const [mode, setMode] = useState<LandingMode>(
    initialRoute.kind === 'run' ? 'auto' : initialRoute.kind,
  );
  const sseRef = useRef<EventSource | null>(null);
  // Track the highest event cursor we've received so we can resume SSE
  // without replaying old events (which was causing log lines to duplicate
  // every time the SSE reconnected via the reopenTimer).
  const sseCursorRef = useRef<number>(0);

  // Sync URL hash whenever (selectedId, mode) changes. Use replaceState for
  // the initial load (so we don't leave an empty history entry), and pushState
  // for user-driven navigation.
  const isFirstUrlSync = useRef(true);
  useEffect(() => {
    const desired = selectedId !== null
      ? routeToHash({ kind: 'run', id: selectedId })
      : routeToHash({ kind: mode });
    if (window.location.hash === desired || (desired === '#/' && !window.location.hash)) return;
    if (isFirstUrlSync.current) {
      isFirstUrlSync.current = false;
      window.history.replaceState(null, '', desired);
    } else {
      window.history.pushState(null, '', desired);
    }
  }, [selectedId, mode]);

  // Listen for browser back/forward — re-parse hash and apply to state.
  useEffect(() => {
    const onPop = () => {
      const r = parseHash(window.location.hash);
      if (r.kind === 'run') {
        setSelectedId(r.id);
      } else {
        setSelectedId(null);
        setMode(r.kind);
      }
    };
    window.addEventListener('popstate', onPop);
    window.addEventListener('hashchange', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      window.removeEventListener('hashchange', onPop);
    };
  }, []);

  const completedSubjects = useMemo(
    () => new Set(runs.filter(r => r.status === 'done').map(r => r.id)),
    [runs],
  );

  const refreshList = useCallback(async () => {
    try {
      const list = await api.list();
      setRuns(list);
    } catch (e) {
      console.error('list failed', e);
    }
  }, []);

  // Belt-and-suspenders: poll the selected run's full state every 3s, so the
  // UI catches up even if the SSE stream closed early (e.g. between job
  // generations during a retry-from-failure).
  useEffect(() => {
    if (!selectedId) return;
    const id = setInterval(() => {
      api.get(selectedId).then(r => {
        setCurrentRun(prev => {
          if (!prev) return r;
          // Don't overwrite the live log_tail we accumulated via SSE.
          return { ...r, log_tail: prev.log_tail };
        });
      }).catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [selectedId]);

  useEffect(() => {
    refreshList();
    const id = setInterval(refreshList, 5000);
    return () => clearInterval(id);
  }, [refreshList]);

  // Whenever selection changes, fetch fresh state + open SSE if active.
  // Also reopens SSE when a new job for the same run_id starts (e.g. retry-from-failure).
  useEffect(() => {
    if (!selectedId) {
      setCurrentRun(null);
      setLogs([]);
      sseRef.current?.close();
      sseRef.current = null;
      sseCursorRef.current = 0;
      return;
    }
    let cancelled = false;
    let lastOpenedActive = false;
    let reopenTimer: number | null = null;
    // Fresh run → reset the event cursor so SSE replays history once.
    sseCursorRef.current = 0;

    const openStream = () => {
      sseRef.current?.close();
      // Resume from the cursor we last saw so reopened streams don't replay
      // already-seen events (which previously duplicated log lines on every
      // reconnect cycle).
      const es = api.openEvents(selectedId, sseCursorRef.current);
      sseRef.current = es;
      es.onmessage = ev => {
        try {
          // Track the event id so subsequent reconnects can skip ahead.
          if (ev.lastEventId) {
            const n = parseInt(ev.lastEventId, 10);
            if (!isNaN(n) && n + 1 > sseCursorRef.current) sseCursorRef.current = n + 1;
          }
          handleEvent(JSON.parse(ev.data));
        } catch {}
      };
      es.onerror = () => {
        // Browser will auto-reconnect on transient errors, but if the server
        // cleanly closes the loop (e.g. status='error' or 'done'), the polling
        // path below picks the next active job back up.
      };
    };

    api.get(selectedId).then(r => {
      if (cancelled) return;
      setCurrentRun(r);
      // DON'T pre-load logs from r.log_tail — SSE will replay the full event
      // history from cursor=0 below, which already includes those same log lines.
      // Pre-loading caused every line in log_tail to appear twice on initial load.
      setLogs([]);
      lastOpenedActive = r.is_active;
      openStream();
    });

    // If polling discovers that the run flipped to active again (e.g. retry),
    // reopen the SSE so live events start flowing again.
    reopenTimer = window.setInterval(() => {
      if (!selectedId) return;
      const cur = sseRef.current;
      const isClosed = !cur || cur.readyState === 2; // CLOSED
      api.get(selectedId).then(r => {
        if (cancelled) return;
        if (r.is_active && !lastOpenedActive) {
          lastOpenedActive = true;
          openStream();
        }
        if (!r.is_active) {
          lastOpenedActive = false;
        }
        if (isClosed && r.is_active) {
          openStream();
        }
      }).catch(() => {});
    }, 4000);

    return () => {
      cancelled = true;
      if (reopenTimer) clearInterval(reopenTimer);
      sseRef.current?.close();
    };
  }, [selectedId]);

  const handleEvent = (e: SseEvent) => {
    if (e.kind === 'log') {
      const line: LogLine = {
        ts: e.ts ?? Date.now() / 1000,
        text: e.payload as string,
      };
      setLogs(prev => {
        const next = [...prev, line];
        return next.length > 1000 ? next.slice(-1000) : next;
      });
      return;
    }
    setCurrentRun(prev => {
      if (!prev) return prev;
      const next = { ...prev };
      if (e.kind === 'step') next.current_step = e.payload;
      if (e.kind === 'progress') next.step_progress = e.payload;
      if (e.kind === 'status') {
        next.status = e.payload;
        next.is_active = e.payload === 'running';
      }
      if (e.kind === 'youtube') next.youtube_url = e.payload;
      if (e.kind === 'artifact') {
        next.artifacts = { ...next.artifacts, ...e.payload };
      }
      if (e.kind === 'awaiting_approval') {
        const clip = (e.payload as any)?.clip ?? null;
        const pv = next.artifacts.product_video;
        if (pv) {
          next.artifacts = {
            ...next.artifacts,
            product_video: {
              ...pv,
              approvals: { ...pv.approvals, awaiting: clip },
            },
          };
        }
      }
      if (e.kind === 'approved') {
        const clip = (e.payload as any)?.clip;
        const pv = next.artifacts.product_video;
        if (pv && typeof clip === 'number') {
          const approved = pv.approvals.approved.includes(clip)
            ? pv.approvals.approved
            : [...pv.approvals.approved, clip].sort((a, b) => a - b);
          next.artifacts = {
            ...next.artifacts,
            product_video: {
              ...pv,
              approvals: {
                ...pv.approvals,
                approved,
                awaiting: pv.approvals.awaiting === clip ? null : pv.approvals.awaiting,
              },
            },
          };
        }
      }
      return next;
    });
    // The artifact scanner emits a full `artifact` event whenever the run dir
    // changes (plan.json, briefs/, starter_*.png, etc.) so the product-video
    // pane refreshes itself on those. The events below are mostly cosmetic
    // markers; we just trigger a list refresh on terminal status changes.
    if (e.kind === 'status' && (e.payload === 'done' || e.payload === 'error' || e.payload === 'cancelled')) {
      refreshList();
    }
    // On plan_ready / clip_brief_ready, the scanner picks up the files within
    // 2s — RunView's plan/briefs effects re-fetch the JSON content when the
    // artifact list updates. No additional fetch needed here.
  };

  const onNewRun = async (opts: any) => {
    const r = await api.start(opts);
    setSelectedId(r.id);
    setLogs([]);
    refreshList();
  };

  const onManualRun = async (subject: string, skipImages: boolean = false,
                             comfyuiEngine?: 'ltx' | 'wan' | 'wan_s2v',
                             clipCount: number = 5,
                             clipDurationS: number = 10,
                             maxWords?: number | null) => {
    const r = await api.startManual(subject, skipImages, comfyuiEngine,
                                    clipCount, clipDurationS, maxWords);
    setSelectedId(r.id);
    setLogs([]);
    refreshList();
  };

  const onCancel = async () => {
    if (!selectedId) return;
    await api.cancel(selectedId);
  };

  const onRetry = async (from_step: any) => {
    if (!selectedId) return;
    await api.retry(selectedId, from_step);
    // Server wiped the bus + bumped reset_epoch — close the existing SSE so
    // the browser reconnects from cursor 0. Without this we keep sending the
    // stale Last-Event-ID and the server has to rewind every reconnect,
    // re-replaying already-displayed events.
    setLogs([]);
    sseCursorRef.current = 0;
    sseRef.current?.close();
    sseRef.current = null;
    // The selectedId-effect's reopenTimer will reopen the stream within 4s.
  };

  const onDelete = async (id: string) => {
    if (!confirm(`Delete run "${id}" and all its files?`)) return;
    await api.remove(id);
    if (selectedId === id) setSelectedId(null);
    refreshList();
  };

  return (
    <div className="h-full flex">
      <Sidebar
        runs={runs}
        selectedId={selectedId}
        mode={mode === 'product' ? 'auto' : mode}
        onSelect={id => { setSelectedId(id); }}
        onNew={() => { setSelectedId(null); setMode('auto'); }}
        onManual={() => { setSelectedId(null); setMode('manual'); }}
        onSettings={() => { setSelectedId(null); setMode('settings'); }}
        onDelete={onDelete}
      />
      <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {selectedId === null ? (
          <div className="flex-1 overflow-y-auto">
            <div className="sticky top-0 z-10 flex w-full border-b border-zinc-800 bg-zinc-950/40 backdrop-blur-md">
              {TABS.map(t => {
                const active = mode === t.mode;
                return (
                  <button
                    key={t.mode}
                    type="button"
                    onClick={() => setMode(t.mode)}
                    className={`px-4 py-2.5 text-sm font-medium border-b-2 transition ${
                      active
                        ? 'border-emerald-500 text-emerald-300 bg-emerald-500/5'
                        : 'border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/50'
                    }`}
                  >
                    {t.label}
                  </button>
                );
              })}
            </div>
            {mode === 'settings' ? (
              <Settings />
            ) : mode === 'product' ? (
              <ProductBriefForm />
            ) : (
              <>
                {mode === 'auto' ? (
                  <NewRunForm
                    subject={pendingSubject}
                    onSubjectChange={setPendingSubject}
                    onSubmit={onNewRun}
                  />
                ) : (
                  <ManualRunForm
                    subject={pendingSubject}
                    onSubjectChange={setPendingSubject}
                    onSubmit={onManualRun}
                  />
                )}
                <TrendingPanel
                  onApply={s => setPendingSubject(s)}
                  completedSubjects={completedSubjects}
                />
                <IdeasPanel
                  onApply={s => setPendingSubject(s)}
                  completedSubjects={completedSubjects}
                />
                <div className="h-8" />
              </>
            )}
          </div>
        ) : currentRun ? (
          <RunView
            run={currentRun}
            logs={logs}
            onCancel={onCancel}
            onRetry={onRetry}
            onClearLogs={() => setLogs([])}
          />
        ) : (
          <div className="p-8 text-zinc-500">Loading run…</div>
        )}
      </main>
    </div>
  );
}
