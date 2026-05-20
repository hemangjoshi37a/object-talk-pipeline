import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type Run, type SseEvent } from './api';
import { Sidebar } from './components/Sidebar';
import { NewRunForm } from './components/NewRunForm';
import { ManualRunForm } from './components/ManualRunForm';
import { RunView } from './components/RunView';
import { IdeasPanel } from './components/IdeasPanel';
import { TrendingPanel } from './components/TrendingPanel';
import { Settings } from './components/Settings';

export default function App() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [currentRun, setCurrentRun] = useState<Run | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [pendingSubject, setPendingSubject] = useState('');
  const [mode, setMode] = useState<'auto' | 'manual' | 'settings'>('auto');
  const sseRef = useRef<EventSource | null>(null);

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
      return;
    }
    let cancelled = false;
    let lastOpenedActive = false;
    let reopenTimer: number | null = null;

    const openStream = () => {
      sseRef.current?.close();
      const es = api.openEvents(selectedId);
      sseRef.current = es;
      es.onmessage = ev => {
        try {
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
      setLogs(r.log_tail || []);
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
      setLogs(prev => {
        const next = [...prev, e.payload as string];
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
      return next;
    });
    if (e.kind === 'status' && (e.payload === 'done' || e.payload === 'error' || e.payload === 'cancelled')) {
      refreshList();
    }
  };

  const onNewRun = async (opts: any) => {
    const r = await api.start(opts);
    setSelectedId(r.id);
    setLogs([]);
    refreshList();
  };

  const onManualRun = async (subject: string) => {
    const r = await api.startManual(subject);
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
    setLogs([]);
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
        mode={mode}
        onSelect={id => { setSelectedId(id); }}
        onNew={() => { setSelectedId(null); setMode('auto'); }}
        onManual={() => { setSelectedId(null); setMode('manual'); }}
        onSettings={() => { setSelectedId(null); setMode('settings'); }}
        onDelete={onDelete}
      />
      <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {selectedId === null ? (
          <div className="flex-1 overflow-y-auto">
            {mode === 'settings' ? (
              <Settings />
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
          />
        ) : (
          <div className="p-8 text-zinc-500">Loading run…</div>
        )}
      </main>
    </div>
  );
}
