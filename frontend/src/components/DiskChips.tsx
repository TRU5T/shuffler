import { bytes, count } from "@/lib/format";
import type { DiskUsage } from "@/lib/types";
import { cn, diskColor, sortDiskNames } from "@/lib/utils";
import { Tooltip } from "@/components/ui/Tooltip";

/**
 * Per-disk size breakdown for one tree node.
 *
 * This is the core answer the app exists to give: not just "this folder is
 * 43 GB" but "16 GB of it is on disk2 and 8 GB is on disk3".
 */
export function DiskChips({
  perDisk,
  max = 5,
  highlight,
  className,
}: {
  perDisk: Record<string, DiskUsage>;
  max?: number;
  highlight?: string | null;
  className?: string;
}) {
  const names = sortDiskNames(Object.keys(perDisk));
  if (names.length === 0) return null;

  const shown = names.slice(0, max);
  const hidden = names.slice(max);

  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {shown.map((name) => {
        const usage = perDisk[name];
        const colour = diskColor(name);
        const dimmed = highlight != null && highlight !== name;
        return (
          <Tooltip key={name} content={`${count(usage.files, "file")} on ${name}`}>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-1.5 py-px text-[11px] transition-opacity",
                "border-zinc-700/60 bg-zinc-800/50",
                dimmed && "opacity-35",
              )}
            >
              <span className={cn("size-1.5 rounded-full", colour.bg)} />
              <span className={cn("font-medium", colour.text)}>{name}</span>
              <span className="tnum text-zinc-400">{bytes(usage.bytes)}</span>
            </span>
          </Tooltip>
        );
      })}
      {hidden.length > 0 && (
        <Tooltip
          content={
            <div className="space-y-0.5">
              {hidden.map((name) => (
                <div key={name} className="flex justify-between gap-3">
                  <span className={diskColor(name).text}>{name}</span>
                  <span className="tnum">{bytes(perDisk[name].bytes)}</span>
                </div>
              ))}
            </div>
          }
        >
          <span className="rounded-md border border-zinc-700/60 bg-zinc-800/50 px-1.5 py-px text-[11px] text-zinc-400">
            +{hidden.length}
          </span>
        </Tooltip>
      )}
    </div>
  );
}
