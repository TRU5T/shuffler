import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ChevronDown,
  ListOrdered,
  RotateCcw,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { useState } from "react";

import { ConflictResolver } from "@/components/ConflictResolver";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { useToast } from "@/components/ui/Toast";
import { useClearQueue, useRemoveJob, useReorderQueue, useRetryJob } from "@/hooks/useShuffler";
import { bytes, count, delta as fmtDelta, percent } from "@/lib/format";
import type { JobPlan, JobStatus, QueuePlan } from "@/lib/types";
import { cn, diskColor, sortDiskNames } from "@/lib/utils";

const STATUS_TONES: Record<JobStatus, "neutral" | "amber" | "emerald" | "rose" | "sky" | "violet"> =
  {
    draft: "amber",
    ready: "emerald",
    running: "sky",
    done: "violet",
    failed: "rose",
    cancelled: "neutral",
  };

const STATUS_LABELS: Record<JobStatus, string> = {
  draft: "needs decisions",
  ready: "ready",
  running: "running",
  done: "done",
  failed: "failed",
  cancelled: "cancelled",
};

/**
 * The plan-as-you-go queue.
 *
 * Each row carries the cumulative disk state after that job, so you can stack
 * several consolidations and watch where each disk ends up before running
 * anything.
 */
export function QueuePanel({
  plan,
  running,
  onStart,
}: {
  plan: QueuePlan | undefined;
  running: boolean;
  onStart: () => void;
}) {
  const toast = useToast();
  const removeJob = useRemoveJob();
  const retryJob = useRetryJob();
  const clearQueue = useClearQueue();
  const reorder = useReorderQueue();
  const [expanded, setExpanded] = useState<string | null>(null);

  const jobs = plan?.jobs ?? [];
  const pending = jobs.filter((j) => j.job.status === "draft" || j.job.status === "ready");

  const move = (index: number, direction: -1 | 1) => {
    const order = jobs.map((j) => j.job.id);
    const next = index + direction;
    if (next < 0 || next >= order.length) return;
    [order[index], order[next]] = [order[next], order[index]];
    reorder.mutate(order, { onError: (error) => toast.error("Could not reorder", String(error)) });
  };

  return (
    <section className="panel flex min-h-0 flex-col">
      <header className="panel-header">
        <div className="flex items-center gap-2">
          <ListOrdered className="size-4 text-zinc-500" />
          <h2 className="text-sm font-semibold text-zinc-200">Queue</h2>
          {jobs.length > 0 && (
            <Badge size="xs" tone="neutral">
              {count(jobs.length, "job")}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          {jobs.length > 0 && !running && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                clearQueue.mutate(undefined, {
                  onError: (error) => toast.error("Could not clear", String(error)),
                })
              }
            >
              <Trash2 className="size-3.5" />
              Clear
            </Button>
          )}
          <Button
            size="sm"
            variant="primary"
            disabled={pending.length === 0 || running}
            onClick={onStart}
          >
            Review and run
          </Button>
        </div>
      </header>

      {plan && jobs.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-zinc-800/80 bg-zinc-900/40 px-4 py-2 text-xs">
          <span className="text-zinc-400">
            <span className="tnum font-medium text-zinc-200">{bytes(plan.total_move_bytes)}</span> to
            relocate
          </span>
          {plan.total_reclaim_bytes > 0 && (
            <span className="text-zinc-400">
              <span className="tnum font-medium text-emerald-300">
                {bytes(plan.total_reclaim_bytes)}
              </span>{" "}
              reclaimed from duplicates
            </span>
          )}
          {plan.blocked_reasons.length > 0 && (
            <Tooltip
              content={
                <ul className="space-y-1">
                  {plan.blocked_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              }
            >
              <span className="ml-auto flex items-center gap-1.5 text-amber-300">
                <TriangleAlert className="size-3.5" />
                {count(plan.blocked_reasons.length, "job")} not ready
              </span>
            </Tooltip>
          )}
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
          <ListOrdered className="size-6 text-zinc-700" />
          <p className="text-sm text-zinc-400">The queue is empty.</p>
          <p className="max-w-xs text-xs text-zinc-600">
            Pick a folder in the browser and choose Consolidate. Queue as many as you like — the
            array view keeps a running total.
          </p>
        </div>
      ) : (
        <div className="scroll-slim min-h-0 flex-1 divide-y divide-zinc-800/60 overflow-y-auto">
          {jobs.map((jobPlan, index) => (
            <JobRow
              key={jobPlan.job.id}
              jobPlan={jobPlan}
              index={index}
              total={jobs.length}
              expanded={expanded === jobPlan.job.id}
              onToggle={() => setExpanded(expanded === jobPlan.job.id ? null : jobPlan.job.id)}
              onMove={move}
              onRemove={() =>
                removeJob.mutate(jobPlan.job.id, {
                  onError: (error) => toast.error("Could not remove", String(error)),
                })
              }
              onRetry={() =>
                retryJob.mutate(jobPlan.job.id, {
                  onSuccess: () =>
                    toast.info(
                      "Back in the queue",
                      "Files that already moved are no longer part of the job.",
                    ),
                  onError: (error) => toast.error("Could not retry", String(error)),
                })
              }
              disableControls={running}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function JobRow({
  jobPlan,
  index,
  total,
  expanded,
  onToggle,
  onMove,
  onRemove,
  onRetry,
  disableControls,
}: {
  jobPlan: JobPlan;
  index: number;
  total: number;
  expanded: boolean;
  onToggle: () => void;
  onMove: (index: number, direction: -1 | 1) => void;
  onRemove: () => void;
  onRetry: () => void;
  disableControls: boolean;
}) {
  const { job, disks_after, blocked_reason, overflow } = jobPlan;
  const colour = diskColor(job.target_disk);
  const isRunning = job.status === "running";
  const hasFailed = job.status === "failed";
  const progress = job.move_bytes > 0 ? percent(job.bytes_done, job.move_bytes + job.reclaim_bytes) : 0;

  return (
    <div className={cn(isRunning && "bg-sky-500/[0.04]")}>
      <div className="flex items-start gap-3 px-4 py-3">
        <span className="tnum mt-0.5 w-5 shrink-0 text-center text-xs text-zinc-600">
          {index + 1}
        </span>

        <button onClick={onToggle} className="min-w-0 flex-1 text-left">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm text-zinc-100">{job.source_relpath || "/"}</span>
            <ArrowRight className="size-3.5 shrink-0 text-zinc-600" />
            <span className={cn("shrink-0 text-sm font-medium", colour.text)}>
              {job.target_disk}
            </span>
            <Badge tone={STATUS_TONES[job.status]} size="xs" className="shrink-0">
              {STATUS_LABELS[job.status]}
            </Badge>
            {job.unresolved_conflicts > 0 && (
              <Badge tone="amber" size="xs" className="shrink-0">
                {count(job.unresolved_conflicts, "decision")} needed
              </Badge>
            )}
            {overflow && (
              <Tooltip content="This job would push the target disk past its reserve">
                <TriangleAlert className="size-3.5 shrink-0 text-rose-400" />
              </Tooltip>
            )}
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-500">
            <span className="tnum">
              {count(job.move_files, "file")} · {bytes(job.move_bytes)} in
            </span>
            {job.delete_files > 0 && (
              <span className="tnum text-emerald-400/80">
                {count(job.delete_files, "copy", "copies")} deleted · {bytes(job.reclaim_bytes)}{" "}
                reclaimed
              </span>
            )}
            {blocked_reason && <span className="text-amber-400/90">{blocked_reason}</span>}
            {job.error && <span className="text-rose-400">{job.error}</span>}
          </div>

          {isRunning && (
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full rounded-full bg-sky-400 transition-[width] duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          )}
        </button>

        <div className="flex shrink-0 items-center gap-0.5">
          <ChevronDown
            className={cn(
              "size-4 text-zinc-600 transition-transform",
              expanded && "rotate-180",
            )}
          />
          {!disableControls && hasFailed && (
            <Tooltip content="Put this job back in the queue">
              <Button size="icon" variant="ghost" onClick={onRetry} aria-label="Retry job">
                <RotateCcw className="size-3.5 text-rose-400" />
              </Button>
            </Tooltip>
          )}
          {!disableControls && (
            <>
              <Button
                size="icon"
                variant="ghost"
                disabled={index === 0}
                onClick={() => onMove(index, -1)}
                aria-label="Move earlier"
              >
                <ArrowUp className="size-3.5" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                disabled={index === total - 1}
                onClick={() => onMove(index, 1)}
                aria-label="Move later"
              >
                <ArrowDown className="size-3.5" />
              </Button>
              <Button size="icon" variant="ghost" onClick={onRemove} aria-label="Remove job">
                <X className="size-3.5" />
              </Button>
            </>
          )}
        </div>
      </div>

      {expanded && (
        <div className="space-y-4 border-t border-zinc-800/60 bg-zinc-950/40 px-4 py-3">
          <div>
            <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              Array state after this job
            </h4>
            <div className="space-y-1">
              {sortDiskNames(Object.keys(disks_after)).map((name) => {
                const projection = disks_after[name];
                const changed = projection.delta !== 0;
                return (
                  <div
                    key={name}
                    className={cn("flex items-center gap-3 text-xs", !changed && "opacity-45")}
                  >
                    <span className="w-16 shrink-0 text-zinc-400">{name}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-800">
                      <div
                        className={cn(
                          "h-full rounded-full",
                          projection.overflow ? "bg-rose-500" : diskColor(name).bg,
                        )}
                        style={{ width: `${percent(projection.used_after, projection.total)}%` }}
                      />
                    </div>
                    <span className="tnum w-20 shrink-0 text-right text-zinc-500">
                      {bytes(projection.free_before)}
                    </span>
                    <ArrowRight className="size-3 shrink-0 text-zinc-700" />
                    <span
                      className={cn(
                        "tnum w-20 shrink-0",
                        projection.overflow
                          ? "font-semibold text-rose-300"
                          : projection.delta > 0
                            ? "text-amber-300"
                            : projection.delta < 0
                              ? "text-emerald-300"
                              : "text-zinc-500",
                      )}
                    >
                      {bytes(projection.free_after)}
                    </span>
                    <span className="tnum w-24 shrink-0 text-right text-zinc-600">
                      {changed ? fmtDelta(projection.delta) : "—"}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {job.conflicts.length > 0 && (
            <div>
              <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                Conflicts
              </h4>
              <ConflictResolver job={job} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
