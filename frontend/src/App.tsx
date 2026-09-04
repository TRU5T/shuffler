import { Radar, Layers, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ConnectionDialog } from "@/components/ConnectionDialog";
import { ConsolidateDialog } from "@/components/ConsolidateDialog";
import { DiskStrip } from "@/components/DiskStrip";
import { DuplicatesView } from "@/components/DuplicatesView";
import { ExecutionPanel } from "@/components/ExecutionPanel";
import { QueuePanel } from "@/components/QueuePanel";
import { RunDialog } from "@/components/RunDialog";
import { TopBar, type View } from "@/components/TopBar";
import { TreeBrowser } from "@/components/TreeBrowser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import {
  keys,
  useActiveScan,
  useConnection,
  useDisks,
  useExecutionState,
  useFragmented,
  useHealth,
  useProgressStream,
  useQueuePlan,
  useScanMutation,
} from "@/hooks/useShuffler";
import { bytes, count } from "@/lib/format";
import type { TreeNode } from "@/lib/types";

export default function App() {
  const toast = useToast();
  const client = useQueryClient();

  const { data: health } = useHealth();
  const { data: connection } = useConnection();
  const { data: scanState } = useActiveScan();
  const { data: plan } = useQueuePlan();
  const { data: execution } = useExecutionState();
  const { events, progress, connected, clear: clearLog } = useProgressStream();
  const rescan = useScanMutation();

  const [view, setView] = useState<View>("browse");
  const [path, setPath] = useState("");
  const [selectedDisk, setSelectedDisk] = useState<string | null>(null);
  const [consolidating, setConsolidating] = useState<TreeNode | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [indexStale, setIndexStale] = useState(false);

  const scan = scanState?.scan ?? null;
  const running = execution?.running ?? false;
  const reserveBytes = connection?.reserve_bytes ?? 0;

  // Polls in the background purely to keep capacity figures current; the strip
  // renders plan.disks_now so its numbers always match the projections.
  const { data: liveDisks } = useDisks(health?.ok ?? false);
  const disks = plan?.disks_now.length
    ? plan.disks_now
    : (liveDisks ?? scan?.disks ?? health?.disks ?? []);

  // A live run invalidates the index it was planned against, so ask for a
  // rescan rather than letting stale sizes drive the next decision.
  const lastEvent = events.length > 0 ? events[events.length - 1] : null;
  const seenRun = useRef<number | null>(null);
  useEffect(() => {
    if (lastEvent?.type === "queue_done" && !lastEvent.dry_run && seenRun.current !== lastEvent.ts) {
      seenRun.current = lastEvent.ts;
      setIndexStale(true);
    }
  }, [lastEvent]);

  const doRescan = () => {
    if (!scan) return;
    rescan.mutate(scan.root, {
      onSuccess: () => {
        setIndexStale(false);
        toast.success("Index rebuilt");
      },
      onError: (error) => toast.error("Rescan failed", String(error)),
    });
  };

  const projections = useMemo(() => {
    if (!plan || plan.jobs.length === 0) return undefined;
    const hasChange = Object.values(plan.final).some((p) => p.delta !== 0);
    return hasChange ? plan.final : undefined;
  }, [plan]);

  const navigate = (next: string) => {
    setPath(next);
    setView("browse");
  };

  const activateScan = async (id: string) => {
    try {
      await api.activateScan(id);
      setPath("");
      client.invalidateQueries();
      toast.success("Switched scan");
    } catch (error) {
      toast.error("Could not load that scan", String(error));
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar
        health={health}
        connection={connection}
        scan={scan}
        scanning={scanState?.scanning ?? false}
        view={view}
        onViewChange={setView}
        onOpenSettings={() => setSettingsOpen(true)}
        onActivateScan={activateScan}
      />

      <main className="scroll-slim min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[110rem] flex-col gap-4 p-4">
          <DiskStrip
            disks={disks}
            projections={projections}
            reserveBytes={reserveBytes}
            selectedDisk={selectedDisk}
            onSelectDisk={setSelectedDisk}
          />

          {indexStale && (
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/30 bg-amber-500/[0.06] px-4 py-3">
              <RefreshCw className="size-4 shrink-0 text-amber-400" />
              <p className="min-w-0 flex-1 text-sm text-amber-100">
                Files have moved, so this index no longer matches the disks. Rescan before planning
                anything else.
              </p>
              <Button variant="primary" size="sm" loading={rescan.isPending} onClick={doRescan}>
                Rescan {scan?.root || "/"}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setIndexStale(false)}>
                Dismiss
              </Button>
            </div>
          )}

          {selectedDisk && (
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <Badge tone="sky" size="sm">
                filtered to {selectedDisk}
              </Badge>
              <span>Only folders and duplicates with data on this disk are shown.</span>
              <Button size="sm" variant="ghost" onClick={() => setSelectedDisk(null)}>
                Clear filter
              </Button>
            </div>
          )}

          {!scan ? (
            <EmptyState connected={health?.ok ?? false} onOpenSettings={() => setSettingsOpen(true)} />
          ) : (
            <div className="grid min-h-0 gap-4 xl:grid-cols-[minmax(0,1.75fr)_minmax(24rem,1fr)]">
              <div className="flex min-h-[34rem] min-w-0 flex-col gap-4">
                {view === "browse" ? (
                  <>
                    <FragmentedHint
                      under={path}
                      onConsolidate={setConsolidating}
                      onNavigate={navigate}
                    />
                    <TreeBrowser
                      path={path}
                      onNavigate={navigate}
                      onConsolidate={setConsolidating}
                      selectedDisk={selectedDisk}
                      scanRoot={scan.root}
                    />
                  </>
                ) : (
                  <DuplicatesView
                    under={path}
                    onOpenFolder={navigate}
                    selectedDisk={selectedDisk}
                  />
                )}
              </div>

              <div className="flex min-w-0 flex-col gap-4">
                <QueuePanel plan={plan} running={running} onStart={() => setRunOpen(true)} />
                <ExecutionPanel
                  state={execution}
                  events={events}
                  progress={progress}
                  connected={connected}
                  onClearLog={clearLog}
                />
              </div>
            </div>
          )}
        </div>
      </main>

      <ConsolidateDialog
        node={consolidating}
        disks={disks}
        reserveBytes={reserveBytes}
        onClose={() => {
          setConsolidating(null);
          client.invalidateQueries({ queryKey: keys.queue });
        }}
      />
      <RunDialog open={runOpen} plan={plan} onClose={() => setRunOpen(false)} />
      <ConnectionDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

/**
 * Surfaces the shallowest folders that are split across disks, which is where
 * consolidation pays off most. Saves hunting through the tree by hand.
 */
function FragmentedHint({
  under,
  onConsolidate,
  onNavigate,
}: {
  under: string;
  onConsolidate: (node: TreeNode) => void;
  onNavigate: (path: string) => void;
}) {
  const { data } = useFragmented(under);
  const top = (data ?? []).slice(0, 4);
  if (top.length === 0) return null;

  return (
    <section className="panel px-4 py-3">
      <h2 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        <Layers className="size-3.5" />
        Split across disks
        <span className="font-normal normal-case tracking-normal text-zinc-600">
          worth consolidating first
        </span>
      </h2>
      <div className="flex flex-wrap gap-2">
        {top.map((node) => (
          <div
            key={node.relpath}
            className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 py-1.5 pl-3 pr-1.5"
          >
            <button
              onClick={() => onNavigate(node.relpath)}
              className="text-left text-xs text-zinc-200 hover:text-white"
            >
              {node.name}
              <span className="tnum ml-2 text-zinc-500">
                {bytes(node.total_bytes)} over {node.disk_count} disks
              </span>
            </button>
            <Button size="sm" variant="outline" onClick={() => onConsolidate(node)}>
              Consolidate
            </Button>
          </div>
        ))}
      </div>
    </section>
  );
}

function EmptyState({
  connected,
  onOpenSettings,
}: {
  connected: boolean;
  onOpenSettings: () => void;
}) {
  return (
    <div className="panel flex flex-col items-center gap-3 px-6 py-20 text-center">
      {connected ? (
        <>
          <Radar className="size-8 text-zinc-700" />
          <h2 className="text-base font-semibold text-zinc-200">Nothing indexed yet</h2>
          <p className="max-w-md text-sm leading-relaxed text-zinc-500">
            Enter a path that exists on your data disks and scan it. Paste a full path like{" "}
            <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-xs text-zinc-300">
              /mnt/disk1/data/media
            </code>{" "}
            and Shuffler will strip the disk part off and walk{" "}
            <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-xs text-zinc-300">
              data/media
            </code>{" "}
            on every disk.
          </p>
          <p className="max-w-md text-xs text-zinc-600">
            {count(0, "file")} indexed. Scanning is read-only.
          </p>
        </>
      ) : (
        <>
          <TriangleAlert className="size-8 text-rose-400/70" />
          <h2 className="text-base font-semibold text-zinc-200">Not connected to any disks</h2>
          <p className="max-w-md text-sm leading-relaxed text-zinc-500">
            Point Shuffler at your Unraid box over SSH, or switch to the local backend if it is
            running on the host itself.
          </p>
          <Button variant="primary" onClick={onOpenSettings}>
            Open connection settings
          </Button>
        </>
      )}
    </div>
  );
}
