import {
  CircleCheck,
  CircleX,
  Copy,
  FlaskConical,
  FolderTree,
  History,
  Loader2,
  Radar,
  Settings,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { useToast } from "@/components/ui/Toast";
import { useScanHistory, useScanMutation } from "@/hooks/useShuffler";
import { bytes, count, duration, relativeTime } from "@/lib/format";
import type { ConnectionSettings, HealthResult, ScanSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

export type View = "browse" | "duplicates";

export function TopBar({
  health,
  connection,
  scan,
  scanning,
  view,
  onViewChange,
  onOpenSettings,
  onActivateScan,
}: {
  health: HealthResult | undefined;
  connection: ConnectionSettings | undefined;
  scan: ScanSummary | null;
  scanning: boolean;
  view: View;
  onViewChange: (view: View) => void;
  onOpenSettings: () => void;
  onActivateScan: (id: string) => void;
}) {
  const toast = useToast();
  const runScan = useScanMutation();
  const { data: history } = useScanHistory();
  const [root, setRoot] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    if (scan?.root !== undefined) setRoot(scan.root);
  }, [scan?.root]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!root.trim()) {
      toast.error("Enter a path to scan", "For example data/media or /mnt/disk1/data/media");
      return;
    }
    runScan.mutate(root, {
      onSuccess: (summary) =>
        toast.success(
          `Scanned ${summary.root || "/"}`,
          `${count(summary.total_files, "file")} across ${count(summary.disks.length, "disk")} in ${duration(summary.duration_seconds)}`,
        ),
      onError: (error) => toast.error("Scan failed", String(error)),
    });
  };

  const busy = scanning || runScan.isPending;

  return (
    <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/85 backdrop-blur">
      <div className="flex flex-wrap items-center gap-3 px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="flex size-8 items-center justify-center rounded-lg bg-zinc-900 ring-1 ring-zinc-800">
            <svg viewBox="0 0 32 32" className="size-5" aria-hidden>
              <rect x="6" y="8" width="20" height="4" rx="2" className="fill-amber-500" />
              <rect x="6" y="14" width="13" height="4" rx="2" className="fill-sky-400" />
              <rect x="6" y="20" width="17" height="4" rx="2" className="fill-emerald-400" />
            </svg>
          </div>
          <div className="leading-tight">
            <h1 className="text-sm font-semibold text-zinc-100">Shuffler</h1>
            <p className="text-[10px] uppercase tracking-wider text-zinc-500">Unraid disks</p>
          </div>
        </div>

        <form onSubmit={submit} className="flex min-w-[18rem] flex-1 items-center gap-2">
          <div className="relative flex-1">
            <FolderTree className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-zinc-500" />
            <input
              value={root}
              onChange={(event) => setRoot(event.target.value)}
              placeholder="data/media"
              spellCheck={false}
              className="h-9 w-full rounded-lg border border-zinc-800 bg-zinc-900 pl-9 pr-3 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-zinc-700"
            />
          </div>
          <Tooltip content="Walk this path on every data disk and rebuild the index">
            <Button type="submit" variant="primary" loading={busy}>
              {!busy && <Radar className="size-4" />}
              {scan ? "Rescan" : "Scan"}
            </Button>
          </Tooltip>
          {history && history.length > 0 && (
            <div className="relative">
              <Button
                type="button"
                size="icon"
                variant="ghost"
                onClick={() => setHistoryOpen((open) => !open)}
                aria-label="Previous scans"
              >
                <History className="size-4" />
              </Button>
              {historyOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setHistoryOpen(false)} />
                  <div className="absolute right-0 top-10 z-50 w-72 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 shadow-xl shadow-black/50">
                    <p className="border-b border-zinc-800 px-3 py-2 text-[11px] uppercase tracking-wide text-zinc-500">
                      Previous scans
                    </p>
                    {history.map((entry) => (
                      <button
                        key={entry.id}
                        onClick={() => {
                          onActivateScan(entry.id);
                          setHistoryOpen(false);
                        }}
                        className={cn(
                          "block w-full px-3 py-2 text-left transition-colors hover:bg-zinc-800",
                          entry.id === scan?.id && "bg-zinc-800/60",
                        )}
                      >
                        <span className="block truncate text-xs text-zinc-200">
                          {entry.root || "/"}
                        </span>
                        <span className="tnum block text-[11px] text-zinc-500">
                          {count(entry.total_files, "file")} · {bytes(entry.total_bytes)} ·{" "}
                          {relativeTime(entry.created_at)}
                        </span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </form>

        <div className="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-0.5">
          <ViewTab
            active={view === "browse"}
            onClick={() => onViewChange("browse")}
            icon={<FolderTree className="size-3.5" />}
            label="Browse"
          />
          <ViewTab
            active={view === "duplicates"}
            onClick={() => onViewChange("duplicates")}
            icon={<Copy className="size-3.5" />}
            label="Duplicates"
            badge={
              scan && scan.dup_wasted_bytes > 0 ? bytes(scan.dup_wasted_bytes) : undefined
            }
          />
        </div>

        <div className="flex items-center gap-2">
          {connection?.dry_run && (
            <Tooltip content="Runs default to dry run: nothing is written until you explicitly ask for a live run.">
              <Badge tone="emerald" size="sm">
                <FlaskConical className="size-3" />
                dry run
              </Badge>
            </Tooltip>
          )}
          <Tooltip content={health?.message ?? "Checking the connection"}>
            <button
              onClick={onOpenSettings}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs transition-colors",
                health?.ok
                  ? "border-emerald-500/30 bg-emerald-500/[0.07] text-emerald-300 hover:bg-emerald-500/[0.12]"
                  : "border-rose-500/30 bg-rose-500/[0.07] text-rose-300 hover:bg-rose-500/[0.12]",
              )}
            >
              {health === undefined ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : health.ok ? (
                <CircleCheck className="size-3.5" />
              ) : (
                <CircleX className="size-3.5" />
              )}
              {health?.ok ? (connection?.storage_backend === "ssh" ? connection.ssh_host : "local") : "offline"}
            </button>
          </Tooltip>
          <Button size="icon" variant="ghost" onClick={onOpenSettings} aria-label="Settings">
            <Settings className="size-4" />
          </Button>
        </div>
      </div>

      {scan && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-zinc-800/60 px-4 py-1.5 text-[11px] text-zinc-500">
          <Tooltip
            content={
              scan.total_files === scan.distinct_files
                ? "Every file exists on exactly one disk."
                : `${count(scan.distinct_files, "distinct file")}, plus ${count(scan.total_files - scan.distinct_files, "extra copy", "extra copies")} on other disks.`
            }
          >
            <span className="tnum">
              <span className="text-zinc-300">{count(scan.total_files, "file")}</span> ·{" "}
              {bytes(scan.total_bytes)}
            </span>
          </Tooltip>
          <span className="tnum">
            {scan.dup_files > 0 ? (
              <>
                <span className="text-rose-300">{count(scan.dup_files, "duplicate")}</span> wasting{" "}
                {bytes(scan.dup_wasted_bytes)}
              </>
            ) : (
              <span className="text-emerald-300">no duplicates</span>
            )}
          </span>
          <span className="tnum">{count(scan.disks.length, "disk")}</span>
          <span className="ml-auto tnum">
            scanned {relativeTime(scan.created_at)} in {duration(scan.duration_seconds)}
          </span>
        </div>
      )}
    </header>
  );
}

function ViewTab({
  active,
  onClick,
  icon,
  label,
  badge,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  badge?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition-colors",
        active ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200",
      )}
    >
      {icon}
      {label}
      {badge && (
        <span className="tnum rounded bg-rose-500/15 px-1 py-px text-[10px] text-rose-300">
          {badge}
        </span>
      )}
    </button>
  );
}
