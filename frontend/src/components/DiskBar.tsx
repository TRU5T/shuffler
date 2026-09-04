import { cn, diskColor } from "@/lib/utils";
import { bytes, percent } from "@/lib/format";
import type { Disk, DiskProjection } from "@/lib/types";
import { Tooltip } from "@/components/ui/Tooltip";

/**
 * One disk's capacity bar.
 *
 * Solid = what is used today. When a projection is supplied the bar also shows
 * the pending change: an animated striped extension for data arriving, and a
 * hollowed-out tail for data leaving. Reading a row tells you both where the
 * disk stands and where the queue will take it.
 */
export function DiskBar({
  disk,
  projection,
  reserveBytes = 0,
  height = "h-2.5",
  className,
}: {
  disk: Disk;
  projection?: DiskProjection;
  reserveBytes?: number;
  height?: string;
  className?: string;
}) {
  const colour = diskColor(disk.name);
  const total = disk.total || 1;

  const usedBefore = disk.used;
  const usedAfter = projection ? projection.used_after : usedBefore;
  const gaining = usedAfter > usedBefore;
  const losing = usedAfter < usedBefore;

  const solid = percent(Math.min(usedBefore, usedAfter), total);
  const changed = percent(Math.abs(usedAfter - usedBefore), total);
  const overflow = projection?.overflow ?? false;

  return (
    <div
      className={cn(
        "relative w-full overflow-hidden rounded-full bg-zinc-800/80 ring-1 ring-inset ring-white/5",
        height,
        className,
      )}
    >
      <div className="flex h-full">
        <Tooltip
          content={`${bytes(Math.min(usedBefore, usedAfter))} in use`}
          side="top"
        >
          <div
            className={cn("h-full", overflow ? "bg-rose-500" : colour.bg)}
            style={{ width: `${solid}%` }}
          />
        </Tooltip>

        {gaining && (
          <Tooltip content={`${bytes(usedAfter - usedBefore)} arriving from the queue`}>
            <div
              className={cn(
                "stripes-animated h-full",
                overflow ? "bg-rose-500/70" : "bg-amber-500/80",
              )}
              style={{ width: `${changed}%` }}
            />
          </Tooltip>
        )}

        {losing && (
          <Tooltip content={`${bytes(usedBefore - usedAfter)} leaving this disk`}>
            <div
              className={cn(
                "h-full border-y border-r border-dashed border-emerald-400/60 bg-emerald-500/15",
                colour.soft,
              )}
              style={{ width: `${changed}%` }}
            />
          </Tooltip>
        )}
      </div>

      {/* The point past which free space would fall below the configured
          reserve, so you can see how much headroom is really left. */}
      {reserveBytes > 0 && reserveBytes < total && (
        <Tooltip content={`Reserve line: ${bytes(reserveBytes)} must stay free`}>
          <div
            className="absolute inset-y-0 w-0.5 bg-white/40"
            style={{ left: `${percent(total - reserveBytes, total)}%` }}
          />
        </Tooltip>
      )}
    </div>
  );
}
