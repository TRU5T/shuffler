import { useVirtualizer } from "@tanstack/react-virtual";
import { Copy, FolderOpen, Loader2, Sparkles } from "lucide-react";
import { useRef, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { useDuplicates } from "@/hooks/useShuffler";
import { bytes, count, splitPath } from "@/lib/format";
import type { DuplicateKind } from "@/lib/types";
import { cn, diskColor, sortDiskNames } from "@/lib/utils";

const ROW_HEIGHT = 60;

const FILTERS: { label: string; value: DuplicateKind | null; hint: string }[] = [
  { label: "All", value: null, hint: "Every file that exists on more than one disk" },
  {
    label: "Identical",
    value: "identical",
    hint: "Same path and same size on every disk: almost certainly a redundant copy",
  },
  {
    label: "Different sizes",
    value: "variant",
    hint: "Same path but different sizes, so these are probably different encodes",
  },
];

export function DuplicatesView({
  under,
  onOpenFolder,
  selectedDisk,
}: {
  under: string;
  onOpenFolder: (relpath: string) => void;
  selectedDisk: string | null;
}) {
  const [kind, setKind] = useState<DuplicateKind | null>(null);
  const { data, isFetching } = useDuplicates(under, kind);
  const scrollRef = useRef<HTMLDivElement>(null);

  const groups = (data?.groups ?? []).filter(
    (group) => !selectedDisk || selectedDisk in group.copies,
  );

  const virtualizer = useVirtualizer({
    count: groups.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });

  return (
    <section className="panel flex min-h-0 flex-1 flex-col">
      <header className="panel-header flex-wrap">
        <div className="flex items-center gap-2">
          <Copy className="size-4 text-zinc-500" />
          <h2 className="text-sm font-semibold text-zinc-200">Duplicates</h2>
          {data && (
            <Badge tone={data.total_wasted_bytes > 0 ? "rose" : "emerald"} size="xs">
              {bytes(data.total_wasted_bytes)} reclaimable
            </Badge>
          )}
          {under && (
            <span className="truncate text-xs text-zinc-500">
              under <span className="text-zinc-400">{under}</span>
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-0.5">
          {FILTERS.map((option) => (
            <Tooltip key={option.label} content={option.hint}>
              <button
                onClick={() => setKind(option.value)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs transition-colors",
                  kind === option.value
                    ? "bg-zinc-800 text-zinc-100"
                    : "text-zinc-400 hover:text-zinc-200",
                )}
              >
                {option.label}
              </button>
            </Tooltip>
          ))}
        </div>
      </header>

      {data && (
        <div className="border-b border-zinc-800/80 bg-zinc-900/40 px-4 py-2 text-xs text-zinc-400">
          {count(groups.length, "group")}
          {groups.length !== data.total_groups && ` of ${data.total_groups}`} · keeping only the
          largest copy of each would free{" "}
          <span className="tnum font-medium text-zinc-200">
            {bytes(groups.reduce((sum, g) => sum + g.wasted_bytes, 0))}
          </span>
        </div>
      )}

      {groups.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-16 text-center">
          {isFetching ? (
            <Loader2 className="size-5 animate-spin text-zinc-600" />
          ) : (
            <>
              <Sparkles className="size-6 text-emerald-500/60" />
              <p className="text-sm text-zinc-400">No duplicates here.</p>
              <p className="max-w-sm text-xs text-zinc-600">
                Nothing under this path exists on more than one disk.
              </p>
            </>
          )}
        </div>
      ) : (
        <div ref={scrollRef} className="scroll-slim min-h-0 flex-1 overflow-y-auto">
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((item) => {
              const group = groups[item.index];
              const { dir, name } = splitPath(group.relpath);
              const largest = Math.max(...Object.values(group.copies));
              return (
                <div
                  key={group.relpath}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: item.size,
                    transform: `translateY(${item.start}px)`,
                  }}
                  className="row-hover group flex h-full items-center gap-3 border-b border-zinc-800/40 px-4"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm text-zinc-100">{name}</span>
                      <Badge
                        tone={group.kind === "identical" ? "rose" : "amber"}
                        size="xs"
                        className="shrink-0"
                      >
                        {group.kind === "identical" ? "identical" : "different sizes"}
                      </Badge>
                    </div>
                    <p className="truncate text-[11px] text-zinc-600">{dir || "(top level)"}</p>
                  </div>

                  <div className="flex shrink-0 items-center gap-1.5">
                    {sortDiskNames(Object.keys(group.copies)).map((disk) => {
                      const size = group.copies[disk];
                      const colour = diskColor(disk);
                      const isLargest = size === largest;
                      return (
                        <Tooltip
                          key={disk}
                          content={
                            isLargest
                              ? `Largest copy — this is the one "keep larger" would keep`
                              : `${bytes(largest - size)} smaller than the largest copy`
                          }
                        >
                          <span
                            className={cn(
                              "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px]",
                              isLargest
                                ? "border-emerald-500/40 bg-emerald-500/10"
                                : "border-zinc-700/60 bg-zinc-800/50 opacity-70",
                            )}
                          >
                            <span className={cn("size-1.5 rounded-full", colour.bg)} />
                            <span className={cn("font-medium", colour.text)}>{disk}</span>
                            <span className="tnum text-zinc-300">{bytes(size)}</span>
                          </span>
                        </Tooltip>
                      );
                    })}
                  </div>

                  <div className="w-24 shrink-0 text-right">
                    <span className="tnum text-sm font-medium text-rose-300">
                      {bytes(group.wasted_bytes)}
                    </span>
                    <p className="text-[10px] text-zinc-600">reclaimable</p>
                  </div>

                  <Tooltip content="Open the containing folder in the browser">
                    <Button
                      size="icon"
                      variant="ghost"
                      className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                      onClick={() => onOpenFolder(dir.replace(/\/$/, ""))}
                      aria-label="Open containing folder"
                    >
                      <FolderOpen className="size-4" />
                    </Button>
                  </Tooltip>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
