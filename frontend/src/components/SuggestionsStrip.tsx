import { ArrowRight, Ban, Plus, Scale, X } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { useToast } from "@/components/ui/Toast";
import { useAddJob, useHideSuggestion, useSuggestions } from "@/hooks/useShuffler";
import { bytes } from "@/lib/format";
import type { DiskProjection, Suggestion } from "@/lib/types";
import { cn, diskColor } from "@/lib/utils";

/**
 * Proposed folder moves that would even out free space.
 *
 * Suggestions are ordinary jobs: add one, skip it for now, or hide the folder.
 * The list is recomputed against the live queue, so accepting the first pick
 * changes what the rest would do.
 */
export function SuggestionsStrip({ enabled }: { enabled: boolean }) {
  const toast = useToast();
  const [skipped, setSkipped] = useState<string[]>([]);
  const { data, isFetching } = useSuggestions(skipped, enabled);
  const addJob = useAddJob();
  const hide = useHideSuggestion();

  if (!enabled || !data) return null;

  const items = data.suggestions;
  if (data.balanced || items.length === 0) {
    if (items.length === 0 && skipped.length > 0) return null;
    return (
      <section className="panel px-4 py-3">
        <p className="flex items-center gap-2 text-sm text-zinc-400">
          <Scale className="size-4 shrink-0 text-zinc-500" />
          {data.headline}
        </p>
      </section>
    );
  }

  return (
    <section className="panel">
      <header className="panel-header">
        <div className="flex min-w-0 items-center gap-2">
          <Scale className="size-4 shrink-0 text-zinc-500" />
          <h2 className="truncate text-sm font-semibold text-zinc-200">{data.headline}</h2>
          <Badge size="xs" tone="neutral">
            {bytes(data.spread_bytes)} spread
          </Badge>
        </div>
        <span className="shrink-0 text-xs text-zinc-500">
          {isFetching ? "Updating…" : "Nothing is queued until you add it"}
        </span>
      </header>

      <ul className="divide-y divide-zinc-800/60">
        {items.map((item) => (
          <SuggestionRow
            key={`${item.source_relpath}->${item.target_disk}`}
            item={item}
            busy={addJob.isPending || hide.isPending}
            onAdd={() =>
              addJob.mutate(
                { source: item.source_relpath, target: item.target_disk },
                {
                  onSuccess: () =>
                    toast.success(
                      "Added to the queue",
                      `${item.source_name} → ${item.target_disk}`,
                    ),
                  onError: (error) => toast.error("Could not queue", String(error)),
                },
              )
            }
            onSkip={() => setSkipped((current) => [...current, item.source_relpath])}
            onHide={() =>
              hide.mutate(item.source_relpath, {
                onSuccess: () =>
                  toast.info("Won’t suggest this folder", item.source_relpath),
                onError: (error) => toast.error("Could not hide", String(error)),
              })
            }
          />
        ))}
      </ul>
    </section>
  );
}

function SuggestionRow({
  item,
  busy,
  onAdd,
  onSkip,
  onHide,
}: {
  item: Suggestion;
  busy: boolean;
  onAdd: () => void;
  onSkip: () => void;
  onHide: () => void;
}) {
  const from = diskColor(item.from_disk);
  const to = diskColor(item.target_disk);
  const fromAfter = item.disks_after[item.from_disk];
  const toAfter = item.disks_after[item.target_disk];

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-zinc-200" title={item.source_relpath}>
          {item.source_name}
          <span className="ml-2 font-normal text-zinc-600">{item.source_relpath}</span>
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
          Move
          <span className="tnum text-zinc-300">{bytes(item.move_bytes)}</span>
          off
          <span className={cn("font-medium", from.text)}>{item.from_disk}</span>
          onto
          <span className={cn("font-medium", to.text)}>{item.target_disk}</span>
          {fromAfter && toAfter && (
            <>
              <span className="mx-1 h-3 w-px shrink-0 bg-zinc-700" aria-hidden />
              <DiskUsedChange name={item.from_disk} projection={fromAfter} />
              <DiskUsedChange name={item.target_disk} projection={toAfter} />
            </>
          )}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Button size="sm" variant="primary" disabled={busy} onClick={onAdd}>
          <Plus className="size-3.5" />
          Add to queue
        </Button>
        <Tooltip content="Skip for now — may come back after a rescan">
          <Button size="sm" variant="ghost" disabled={busy} onClick={onSkip}>
            <X className="size-3.5" />
            Skip
          </Button>
        </Tooltip>
        <Tooltip content="Don’t suggest this folder again">
          <Button size="sm" variant="ghost" disabled={busy} onClick={onHide}>
            <Ban className="size-3.5" />
            Not this folder
          </Button>
        </Tooltip>
      </div>
    </li>
  );
}

function DiskUsedChange({ name, projection }: { name: string; projection: DiskProjection }) {
  const colour = diskColor(name);
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn("font-medium", colour.text)}>{name}</span>
      <span className="tnum text-zinc-500">{bytes(projection.used_before)}</span>
      <ArrowRight className="size-3 shrink-0 text-zinc-600" />
      <span
        className={cn(
          "tnum font-medium",
          projection.overflow
            ? "text-rose-300"
            : projection.delta > 0
              ? "text-amber-300"
              : "text-emerald-300",
        )}
      >
        {bytes(projection.used_after)}
      </span>
    </span>
  );
}
