import { Check, CircleHelp, Copy, SkipForward, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { useToast } from "@/components/ui/Toast";
import { useResolveAll, useResolveConflict } from "@/hooks/useShuffler";
import { bytes, count, splitPath } from "@/lib/format";
import type { Conflict, ConflictMode, Job } from "@/lib/types";
import { cn, diskColor, sortDiskNames } from "@/lib/utils";

const MODE_LABELS: Record<ConflictMode, string> = {
  keep_larger: "Keep larger",
  keep_smaller: "Keep smaller",
  keep_disk: "Keep one disk",
  keep_both: "Keep both",
  skip: "Leave alone",
};

const MODE_HELP: Record<ConflictMode, string> = {
  keep_larger: "Move the largest copy to the target disk and delete the rest.",
  keep_smaller: "Move the smallest copy to the target disk and delete the rest.",
  keep_disk: "Keep the copy from one specific disk and delete the others.",
  keep_both:
    "Move every copy to the target disk, renaming the extras so nothing is overwritten or lost.",
  skip: "Do nothing with this file. It stays exactly where it is, on every disk.",
};

/**
 * Per-conflict decisions.
 *
 * Nothing here has a default. A 3.8 GB and an 888 MB copy of the same episode
 * might be a redundant duplicate or two deliberate encodes, and only the user
 * knows which, so a job stays blocked until every row has an explicit answer.
 */
export function ConflictResolver({ job }: { job: Job }) {
  const toast = useToast();
  const resolve = useResolveConflict();
  const resolveAll = useResolveAll();
  const [onlyUnresolved, setOnlyUnresolved] = useState(false);

  const unresolved = job.unresolved_conflicts;
  const visible = useMemo(
    () => (onlyUnresolved ? job.conflicts.filter((c) => !c.resolved) : job.conflicts),
    [job.conflicts, onlyUnresolved],
  );

  const bulk = (mode: ConflictMode | null, keepDisk?: string) => {
    resolveAll.mutate(
      { jobId: job.id, mode, keepDisk, onlyUnresolved: true },
      {
        onError: (error) => toast.error("Could not apply to all", String(error)),
      },
    );
  };

  if (job.conflicts.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center">
        <Check className="size-6 text-emerald-400" />
        <p className="text-sm text-zinc-300">No conflicts in this job.</p>
        <p className="max-w-sm text-xs text-zinc-500">
          Nothing under this folder exists on more than one disk, so every file can simply be
          relocated.
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
        <div className="mr-auto flex items-center gap-2">
          <TriangleAlert
            className={cn("size-4", unresolved > 0 ? "text-amber-400" : "text-emerald-400")}
          />
          <span className="text-sm text-zinc-200">
            {unresolved > 0
              ? `${count(unresolved, "file")} still need a decision`
              : "Every conflict has a decision"}
          </span>
          <Badge size="xs" tone="neutral">
            {count(job.conflicts.length, "conflict")}
          </Badge>
        </div>

        <span className="text-xs text-zinc-500">Apply to all undecided:</span>
        <Tooltip content={MODE_HELP.keep_larger}>
          <Button size="sm" variant="outline" onClick={() => bulk("keep_larger")}>
            Keep larger
          </Button>
        </Tooltip>
        <Tooltip content={MODE_HELP.keep_smaller}>
          <Button size="sm" variant="outline" onClick={() => bulk("keep_smaller")}>
            Keep smaller
          </Button>
        </Tooltip>
        <Tooltip content={MODE_HELP.skip}>
          <Button size="sm" variant="outline" onClick={() => bulk("skip")}>
            Leave alone
          </Button>
        </Tooltip>
        <Tooltip content="Clear every decision and start again">
          <Button size="sm" variant="ghost" onClick={() => bulk(null)}>
            Reset
          </Button>
        </Tooltip>
      </div>

      <label className="flex items-center gap-2 text-xs text-zinc-400">
        <input
          type="checkbox"
          checked={onlyUnresolved}
          onChange={(event) => setOnlyUnresolved(event.target.checked)}
          className="size-3.5 accent-amber-500"
        />
        Show only the undecided
      </label>

      <div className="scroll-slim min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        {visible.map((conflict) => (
          <ConflictRow
            key={conflict.relpath}
            conflict={conflict}
            targetDisk={job.target_disk}
            onChange={(mode, keepDisk) =>
              resolve.mutate(
                { jobId: job.id, relpath: conflict.relpath, mode, keepDisk },
                { onError: (error) => toast.error("Could not save that choice", String(error)) }
              )
            }
          />
        ))}
        {visible.length === 0 && (
          <p className="py-8 text-center text-sm text-zinc-500">
            Nothing undecided left. Untick the filter to review your choices.
          </p>
        )}
      </div>
    </div>
  );
}

function ConflictRow({
  conflict,
  targetDisk,
  onChange,
}: {
  conflict: Conflict;
  targetDisk: string;
  onChange: (mode: ConflictMode | null, keepDisk?: string) => void;
}) {
  const { dir, name } = splitPath(conflict.relpath);
  const disks = sortDiskNames(Object.keys(conflict.copies));
  const largest = Math.max(...Object.values(conflict.copies));
  const smallest = Math.min(...Object.values(conflict.copies));

  return (
    <div
      className={cn(
        "rounded-xl border p-3 transition-colors",
        conflict.resolved
          ? "border-zinc-800 bg-zinc-900/40"
          : "border-amber-500/30 bg-amber-500/[0.04]",
      )}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-zinc-100">{name}</span>
            <Badge tone={conflict.kind === "identical" ? "rose" : "amber"} size="xs">
              {conflict.kind === "identical" ? "identical copies" : "different sizes"}
            </Badge>
            {conflict.resolved ? (
              <Badge tone="emerald" size="xs">
                <Check className="size-2.5" />
                {conflict.mode === "skip"
                  ? "left alone"
                  : conflict.mode === "keep_both"
                    ? "keeping both"
                    : `keeping ${conflict.survivor}`}
              </Badge>
            ) : (
              <Badge tone="amber" size="xs">
                needs a decision
              </Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-zinc-600">{dir || "(top level)"}</p>
        </div>
        <span className="tnum shrink-0 text-xs text-rose-300">
          {bytes(Object.values(conflict.copies).reduce((a, b) => a + b, 0) - largest)} wasted
        </span>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        {disks.map((disk) => {
          const size = conflict.copies[disk];
          const colour = diskColor(disk);
          const surviving = conflict.survivor === disk;
          const doomed = conflict.resolved && conflict.mode !== "skip" && conflict.mode !== "keep_both" && !surviving;
          return (
            <Tooltip
              key={disk}
              content={
                <div className="space-y-1">
                  <div>
                    {bytes(size)} on {disk}
                    {size === largest && disks.length > 1 && " (largest)"}
                    {size === smallest && largest !== smallest && " (smallest)"}
                    {disk === targetDisk && " — this is the target disk"}
                  </div>
                  <div className="text-zinc-400">Click to keep this copy and delete the others.</div>
                </div>
              }
            >
              <button
                onClick={() => onChange("keep_disk", disk)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] transition-all",
                  surviving
                    ? "border-emerald-500/60 bg-emerald-500/15 ring-1 ring-emerald-500/30"
                    : "border-zinc-700/60 bg-zinc-800/50 hover:border-zinc-600",
                  doomed && "opacity-40 line-through decoration-rose-400/70",
                )}
              >
                <span className={cn("size-1.5 rounded-full", colour.bg)} />
                <span className={cn("font-medium", colour.text)}>{disk}</span>
                <span className="tnum text-zinc-300">{bytes(size)}</span>
                {disk === targetDisk && <span className="text-zinc-500">target</span>}
              </button>
            </Tooltip>
          );
        })}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1">
        {(["keep_larger", "keep_smaller", "keep_both", "skip"] as ConflictMode[]).map((mode) => (
          <Tooltip key={mode} content={MODE_HELP[mode]}>
            <button
              onClick={() => onChange(conflict.mode === mode ? null : mode)}
              className={cn(
                "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] transition-colors",
                conflict.mode === mode
                  ? "border-amber-500/50 bg-amber-500/15 text-amber-200"
                  : "border-zinc-700/60 text-zinc-300 hover:border-zinc-600 hover:bg-zinc-800/60",
              )}
            >
              {mode === "keep_both" && <Copy className="size-3" />}
              {mode === "skip" && <SkipForward className="size-3" />}
              {MODE_LABELS[mode]}
            </button>
          </Tooltip>
        ))}
        <Tooltip
          content={
            <div className="space-y-1">
              <p>
                <strong>Identical</strong> means the copies are the same size, so one is almost
                certainly redundant.
              </p>
              <p>
                <strong>Different sizes</strong> usually means different encodes or a partial file,
                which is worth a closer look before deleting anything.
              </p>
            </div>
          }
        >
          <span className="ml-auto text-zinc-600">
            <CircleHelp className="size-3.5" />
          </span>
        </Tooltip>
      </div>
    </div>
  );
}
