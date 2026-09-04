import { ArrowRight, Check, HardDrive, Loader2, Merge, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ConflictResolver } from "@/components/ConflictResolver";
import { DiskBar } from "@/components/DiskBar";
import { DiskChips } from "@/components/DiskChips";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Tooltip } from "@/components/ui/Tooltip";
import { useToast } from "@/components/ui/Toast";
import { useAddJob, usePreview, useQueuePlan } from "@/hooks/useShuffler";
import { bytes, count, delta as fmtDelta } from "@/lib/format";
import type { Disk, TreeNode } from "@/lib/types";
import { cn, diskColor, sortDiskNames } from "@/lib/utils";

/**
 * Choose a destination for a folder and see the cost before committing.
 *
 * The dialog has two phases: pick a target and review the projection, then
 * resolve any conflicts on the job that was just queued.
 */
export function ConsolidateDialog({
  node,
  disks,
  reserveBytes,
  onClose,
}: {
  node: TreeNode | null;
  disks: Disk[];
  reserveBytes: number;
  onClose: () => void;
}) {
  const toast = useToast();
  const addJob = useAddJob();
  const { data: plan } = useQueuePlan();
  const [target, setTarget] = useState<string | null>(null);
  const [queuedJobId, setQueuedJobId] = useState<string | null>(null);

  // Default to the disk that already holds the most of this folder: it is the
  // choice that moves the least data.
  const suggested = useMemo(() => {
    if (!node) return null;
    const entries = Object.entries(node.per_disk);
    if (entries.length === 0) return disks[0]?.name ?? null;
    return entries.sort((a, b) => b[1].bytes - a[1].bytes)[0][0];
  }, [node, disks]);

  useEffect(() => {
    if (node) {
      setTarget(suggested);
      setQueuedJobId(null);
    }
  }, [node, suggested]);

  const { data: preview, isFetching: previewing } = usePreview(node?.relpath ?? null, target);

  const queuedJob = queuedJobId
    ? (plan?.jobs.find((jobPlan) => jobPlan.job.id === queuedJobId)?.job ?? null)
    : null;

  const handleAdd = () => {
    if (!node || !target) return;
    addJob.mutate(
      { source: node.relpath, target },
      {
        onSuccess: (result) => {
          const created = result.jobs[result.jobs.length - 1]?.job;
          if (created && created.conflicts.length > 0) {
            setQueuedJobId(created.id);
            toast.info(
              "Added to the queue",
              `${count(created.conflicts.length, "conflict")} need a decision before it can run.`,
            );
          } else {
            toast.success("Added to the queue", `${node.relpath} → ${target}`);
            onClose();
          }
        },
        onError: (error) => toast.error("Could not queue that", String(error)),
      },
    );
  };

  return (
    <Dialog open={node !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="w-[min(64rem,94vw)]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Merge className="size-4 text-amber-400" />
            {queuedJob ? "Resolve conflicts" : "Consolidate folder"}
          </DialogTitle>
          <DialogDescription className="break-all font-mono text-xs">
            {node?.relpath}
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-4">
          {node && !queuedJob && (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3">
                <span className="tnum text-lg font-semibold text-zinc-100">
                  {bytes(node.total_bytes)}
                </span>
                <span className="text-xs text-zinc-500">{count(node.total_files, "file")}</span>
                {node.disk_count > 1 ? (
                  <Badge tone="amber" size="xs">
                    spread over {node.disk_count} disks
                  </Badge>
                ) : (
                  <Badge tone="emerald" size="xs">
                    already on one disk
                  </Badge>
                )}
                <DiskChips perDisk={node.per_disk} max={8} className="ml-auto" />
              </div>

              <section>
                <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  <HardDrive className="size-3.5" />
                  Move everything to
                </h3>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {disks.map((disk) => {
                    const already = node.per_disk[disk.name]?.bytes ?? 0;
                    // Before conflicts are decided this is an upper bound: any
                    // duplicate that gets deleted will reduce it.
                    const upperBound = node.total_bytes - already;
                    const wontFit = upperBound > disk.free - reserveBytes;
                    const selected = target === disk.name;
                    const colour = diskColor(disk.name);
                    return (
                      <button
                        key={disk.name}
                        onClick={() => setTarget(disk.name)}
                        className={cn(
                          "rounded-xl border px-3 py-2.5 text-left transition-all",
                          selected
                            ? "border-amber-500/60 bg-amber-500/[0.07] ring-1 ring-amber-500/30"
                            : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700",
                        )}
                      >
                        <div className="flex items-center gap-2">
                          <span className={cn("size-2 rounded-full", colour.bg)} />
                          <span className="text-sm font-medium text-zinc-100">{disk.name}</span>
                          {disk.name === suggested && (
                            <Tooltip content="Already holds the most of this folder, so this moves the least data">
                              <Badge tone="emerald" size="xs">
                                least to move
                              </Badge>
                            </Tooltip>
                          )}
                          {wontFit && (
                            <Tooltip content="Not enough free space once the reserve is honoured">
                              <TriangleAlert className="ml-auto size-3.5 text-rose-400" />
                            </Tooltip>
                          )}
                          {selected && !wontFit && (
                            <Check className="ml-auto size-3.5 text-amber-400" />
                          )}
                        </div>
                        <div className="mt-2">
                          <DiskBar disk={disk} reserveBytes={reserveBytes} height="h-1.5" />
                        </div>
                        <p className="tnum mt-1.5 text-[11px] text-zinc-500">
                          {bytes(disk.free)} free · up to {bytes(upperBound)} to move in
                        </p>
                      </button>
                    );
                  })}
                </div>
              </section>

              {preview && target && (
                <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
                  <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Effect of this job
                    {previewing && <Loader2 className="size-3 animate-spin" />}
                  </h3>

                  <div className="mb-4 grid gap-3 sm:grid-cols-3">
                    <Stat label="Files relocated" value={preview.move_files.toLocaleString()} sub={bytes(preview.move_bytes)} />
                    <Stat
                      label="Redundant copies deleted"
                      value={preview.delete_files.toLocaleString()}
                      sub={`${bytes(preview.reclaim_bytes)} reclaimed`}
                      tone={preview.delete_files > 0 ? "rose" : undefined}
                    />
                    <Stat
                      label="Conflicts to decide"
                      value={preview.conflicts.length.toLocaleString()}
                      sub={preview.conflicts.length > 0 ? "required before it can run" : "none"}
                      tone={preview.conflicts.length > 0 ? "amber" : undefined}
                    />
                  </div>

                  <div className="space-y-1.5">
                    {sortDiskNames(Object.keys(preview.disks_after))
                      .map((name) => preview.disks_after[name])
                      .filter((projection) => projection.delta !== 0 || projection.name === target)
                      .map((projection) => {
                        const disk = disks.find((d) => d.name === projection.name);
                        if (!disk) return null;
                        return (
                          <div key={projection.name} className="flex items-center gap-3 text-xs">
                            <span className="w-16 shrink-0 text-zinc-400">{projection.name}</span>
                            <DiskBar
                              disk={{ ...disk, used: projection.used_before, free: projection.free_before }}
                              projection={projection}
                              reserveBytes={reserveBytes}
                              className="flex-1"
                              height="h-2"
                            />
                            <span className="tnum w-20 shrink-0 text-right text-zinc-500">
                              {bytes(projection.free_before)}
                            </span>
                            <ArrowRight className="size-3 shrink-0 text-zinc-600" />
                            <span
                              className={cn(
                                "tnum w-20 shrink-0 font-semibold",
                                projection.overflow
                                  ? "text-rose-300"
                                  : projection.delta > 0
                                    ? "text-amber-300"
                                    : "text-emerald-300",
                              )}
                            >
                              {bytes(projection.free_after)}
                            </span>
                            <span className="tnum w-24 shrink-0 text-right text-zinc-500">
                              {fmtDelta(projection.delta)}
                            </span>
                          </div>
                        );
                      })}
                  </div>

                  {preview.overflow && (
                    <p className="mt-3 flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                      <TriangleAlert className="mt-px size-3.5 shrink-0" />
                      {preview.disks_after[target] && preview.disks_after[target].free_after < 0
                        ? `${target} does not have room for this. Pick another disk, or free space first.`
                        : `This would leave ${target} with less than the ${bytes(reserveBytes)} reserve free.`}
                    </p>
                  )}

                  {preview.blocked_reason && !preview.overflow && (
                    <p className="mt-3 rounded-lg border border-zinc-700 bg-zinc-800/60 px-3 py-2 text-xs text-zinc-300">
                      {preview.blocked_reason}
                    </p>
                  )}

                  {plan && plan.jobs.length > 0 && (
                    <p className="mt-3 text-[11px] text-zinc-500">
                      These figures assume the {count(plan.jobs.length, "job")} already in the queue
                      run first.
                    </p>
                  )}
                </section>
              )}
            </>
          )}

          {queuedJob && <ConflictResolver job={queuedJob} />}
        </DialogBody>

        <DialogFooter>
          {queuedJob ? (
            <>
              <span className="mr-auto text-xs text-zinc-500">
                {queuedJob.unresolved_conflicts > 0
                  ? `${count(queuedJob.unresolved_conflicts, "decision")} left. The job stays in the queue either way.`
                  : "All decided. This job is ready to run."}
              </span>
              <Button variant="primary" onClick={onClose}>
                {queuedJob.unresolved_conflicts > 0 ? "Finish later" : "Done"}
              </Button>
            </>
          ) : (
            <>
              <span className="mr-auto text-xs text-zinc-500">
                Nothing is moved until you start the queue.
              </span>
              <Button variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={addJob.isPending}
                disabled={!target || previewing || preview?.move_files === 0 && preview?.delete_files === 0 && preview?.conflicts.length === 0}
                onClick={handleAdd}
              >
                Add to queue
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "rose" | "amber";
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p
        className={cn(
          "tnum mt-0.5 text-xl font-semibold",
          tone === "rose" ? "text-rose-300" : tone === "amber" ? "text-amber-300" : "text-zinc-100",
        )}
      >
        {value}
      </p>
      {sub && <p className="tnum text-[11px] text-zinc-500">{sub}</p>}
    </div>
  );
}
