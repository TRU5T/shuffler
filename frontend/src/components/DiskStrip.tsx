import { ArrowRight, HardDrive, TriangleAlert } from "lucide-react";

import { DiskBar } from "@/components/DiskBar";
import { Badge } from "@/components/ui/Badge";
import { Tooltip } from "@/components/ui/Tooltip";
import { bytes, delta as fmtDelta, percent } from "@/lib/format";
import type { Disk, DiskProjection } from "@/lib/types";
import { cn, diskColor } from "@/lib/utils";

/**
 * The always-visible array overview.
 *
 * Every other view answers "what should I move"; this one answers "can I
 * afford to", both now and after everything in the queue has run.
 */
export function DiskStrip({
  disks,
  projections,
  reserveBytes = 0,
  selectedDisk,
  onSelectDisk,
  className,
}: {
  disks: Disk[];
  projections?: Record<string, DiskProjection>;
  reserveBytes?: number;
  selectedDisk?: string | null;
  onSelectDisk?: (name: string | null) => void;
  className?: string;
}) {
  if (disks.length === 0) {
    return (
      <div className={cn("panel px-4 py-6 text-center text-sm text-zinc-500", className)}>
        No disks detected yet. Check the connection settings.
      </div>
    );
  }

  const hasProjection = projections
    ? Object.values(projections).some((p) => p.delta !== 0)
    : false;
  const totalFreeNow = disks.reduce((sum, d) => sum + d.free, 0);
  const totalFreeAfter = projections
    ? disks.reduce((sum, d) => sum + (projections[d.name]?.free_after ?? d.free), 0)
    : totalFreeNow;

  return (
    <section className={cn("panel", className)}>
      <header className="panel-header">
        <div className="flex items-center gap-2">
          <HardDrive className="size-4 text-zinc-500" />
          <h2 className="text-sm font-semibold text-zinc-200">Array</h2>
          <Badge size="xs" tone="neutral">
            {disks.length} disks
          </Badge>
        </div>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <span className="tnum">{bytes(totalFreeNow)} free</span>
          {hasProjection && (
            <>
              <ArrowRight className="size-3 text-zinc-600" />
              <span
                className={cn(
                  "tnum font-medium",
                  totalFreeAfter >= totalFreeNow ? "text-emerald-300" : "text-amber-300",
                )}
              >
                {bytes(totalFreeAfter)}
              </span>
            </>
          )}
        </div>
      </header>

      <div className="divide-y divide-zinc-800/60">
        {disks.map((disk) => {
          const projection = projections?.[disk.name];
          const colour = diskColor(disk.name);
          const changed = (projection?.delta ?? 0) !== 0;
          const selected = selectedDisk === disk.name;
          const usedPercentAfter = percent(projection?.used_after ?? disk.used, disk.total || 1);

          return (
            <div
              key={disk.name}
              role={onSelectDisk ? "button" : undefined}
              tabIndex={onSelectDisk ? 0 : undefined}
              onClick={() => onSelectDisk?.(selected ? null : disk.name)}
              onKeyDown={(event) => {
                if (onSelectDisk && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault();
                  onSelectDisk(selected ? null : disk.name);
                }
              }}
              className={cn(
                "flex items-center gap-3 px-4 py-2.5 transition-colors",
                onSelectDisk && "cursor-pointer hover:bg-zinc-800/40",
                selected && "bg-zinc-800/60",
              )}
            >
              <div className="flex w-24 shrink-0 items-center gap-2">
                <span className={cn("size-2 shrink-0 rounded-full", colour.bg)} />
                <span className={cn("text-sm font-medium", selected ? "text-zinc-50" : "text-zinc-300")}>
                  {disk.name}
                </span>
              </div>

              <DiskBar
                disk={disk}
                projection={projection}
                reserveBytes={reserveBytes}
                className="flex-1"
              />

              <div className="flex w-14 shrink-0 justify-end">
                <span className="tnum text-xs text-zinc-500">{usedPercentAfter.toFixed(0)}%</span>
              </div>

              <div className="flex w-[13.5rem] shrink-0 items-center justify-end gap-1.5 text-xs">
                {changed && projection ? (
                  <>
                    <span className="tnum text-zinc-500 line-through decoration-zinc-600">
                      {bytes(disk.free)}
                    </span>
                    <ArrowRight className="size-3 shrink-0 text-zinc-600" />
                    <span
                      className={cn(
                        "tnum font-semibold",
                        projection.overflow
                          ? "text-rose-300"
                          : projection.delta > 0
                            ? "text-amber-300"
                            : "text-emerald-300",
                      )}
                    >
                      {bytes(projection.free_after)}
                    </span>
                    <Tooltip content={`${fmtDelta(projection.delta)} on this disk`}>
                      <span
                        className={cn(
                          "tnum ml-0.5 rounded px-1 py-px text-[10px]",
                          projection.delta > 0
                            ? "bg-amber-500/15 text-amber-300"
                            : "bg-emerald-500/15 text-emerald-300",
                        )}
                      >
                        {fmtDelta(projection.delta)}
                      </span>
                    </Tooltip>
                    {projection.overflow && (
                      <Tooltip
                        content={
                          projection.free_after < 0
                            ? "This disk would run out of space."
                            : `This would leave less than the ${bytes(reserveBytes)} reserve free.`
                        }
                      >
                        <TriangleAlert className="size-3.5 shrink-0 text-rose-400" />
                      </Tooltip>
                    )}
                  </>
                ) : (
                  <span className="tnum text-zinc-400">
                    {bytes(disk.free)} free
                    <span className="text-zinc-600"> of {bytes(disk.total)}</span>
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
