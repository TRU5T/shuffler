import { useVirtualizer } from "@tanstack/react-virtual";
import {
  ChevronRight,
  Copy,
  FileVideo,
  Folder,
  FolderTree,
  Home,
  Layers,
  Loader2,
  Merge,
  Search,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { DiskChips } from "@/components/DiskChips";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { useTree } from "@/hooks/useShuffler";
import { bytes, count } from "@/lib/format";
import type { TreeNode } from "@/lib/types";
import { cn } from "@/lib/utils";

const ROW_HEIGHT = 52;

export function TreeBrowser({
  path,
  onNavigate,
  onConsolidate,
  selectedDisk,
  scanRoot,
}: {
  path: string;
  onNavigate: (path: string) => void;
  onConsolidate: (node: TreeNode) => void;
  selectedDisk: string | null;
  scanRoot: string;
}) {
  const { data, isFetching, error } = useTree(path);
  const [filter, setFilter] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => {
    let children = data?.children ?? [];
    if (selectedDisk) {
      children = children.filter((node) => node.per_disk[selectedDisk]);
    }
    const needle = filter.trim().toLowerCase();
    if (needle) {
      children = children.filter((node) => node.name.toLowerCase().includes(needle));
    }
    return children;
  }, [data?.children, selectedDisk, filter]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });

  const crumbs = data?.breadcrumbs ?? [];

  return (
    <section className="panel flex min-h-0 flex-1 flex-col">
      <header className="panel-header flex-wrap">
        <nav className="flex min-w-0 flex-1 items-center gap-1 text-sm" aria-label="Breadcrumb">
          <button
            onClick={() => onNavigate("")}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 transition-colors hover:bg-zinc-800",
              path === "" ? "text-zinc-100" : "text-zinc-400",
            )}
          >
            <Home className="size-3.5" />
            <span className="font-medium">{scanRoot || "/"}</span>
          </button>
          {crumbs.map((crumb, index) => (
            <span key={crumb.relpath} className="flex min-w-0 items-center">
              <ChevronRight className="size-3.5 shrink-0 text-zinc-700" />
              <button
                onClick={() => onNavigate(crumb.relpath)}
                className={cn(
                  "truncate rounded-md px-1.5 py-1 transition-colors hover:bg-zinc-800",
                  index === crumbs.length - 1 ? "text-zinc-100" : "text-zinc-400",
                )}
              >
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-zinc-500" />
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter this folder"
              className="h-8 w-52 rounded-lg border border-zinc-700 bg-zinc-900 pl-8 pr-2.5 text-xs text-zinc-100 placeholder:text-zinc-500 focus:border-zinc-600"
            />
          </div>
          {data?.node && data.node.relpath !== "" && (
            <Button size="sm" variant="primary" onClick={() => onConsolidate(data.node)}>
              <Merge className="size-3.5" />
              Consolidate this folder
            </Button>
          )}
        </div>
      </header>

      {data?.node && <FolderSummary node={data.node} selectedDisk={selectedDisk} />}

      {error && (
        <div className="px-4 py-10 text-center text-sm text-rose-300">{String(error)}</div>
      )}

      {!error && rows.length === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-16 text-center">
          {isFetching ? (
            <Loader2 className="size-5 animate-spin text-zinc-600" />
          ) : (
            <>
              <FolderTree className="size-6 text-zinc-700" />
              <p className="text-sm text-zinc-500">
                {filter || selectedDisk
                  ? "Nothing here matches the current filter."
                  : "This folder is empty."}
              </p>
            </>
          )}
        </div>
      )}

      {rows.length > 0 && (
        <div ref={scrollRef} className="scroll-slim min-h-0 flex-1 overflow-y-auto">
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((item) => {
              const node = rows[item.index];
              return (
                <div
                  key={node.relpath}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: item.size,
                    transform: `translateY(${item.start}px)`,
                  }}
                >
                  <TreeRow
                    node={node}
                    selectedDisk={selectedDisk}
                    onNavigate={onNavigate}
                    onConsolidate={onConsolidate}
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

function FolderSummary({
  node,
  selectedDisk,
}: {
  node: TreeNode;
  selectedDisk: string | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-zinc-800/80 bg-zinc-900/40 px-4 py-2.5">
      <span className="tnum text-sm font-medium text-zinc-200">{bytes(node.total_bytes)}</span>
      <Tooltip
        content={
          node.total_files === node.distinct_files
            ? "No file here exists on more than one disk."
            : `${count(node.distinct_files, "distinct file")}, plus ${count(node.total_files - node.distinct_files, "extra copy", "extra copies")}.`
        }
      >
        <span className="text-xs text-zinc-500">{count(node.total_files, "file")}</span>
      </Tooltip>
      {node.disk_count > 1 && (
        <Badge tone="amber" size="xs">
          <Layers className="size-3" />
          split across {node.disk_count} disks
        </Badge>
      )}
      {node.dup_files > 0 && (
        <Badge tone="rose" size="xs">
          <Copy className="size-3" />
          {count(node.dup_files, "duplicate")} · {bytes(node.dup_wasted_bytes)} reclaimable
        </Badge>
      )}
      <DiskChips perDisk={node.per_disk} max={8} highlight={selectedDisk} className="ml-auto" />
    </div>
  );
}

function TreeRow({
  node,
  selectedDisk,
  onNavigate,
  onConsolidate,
}: {
  node: TreeNode;
  selectedDisk: string | null;
  onNavigate: (path: string) => void;
  onConsolidate: (node: TreeNode) => void;
}) {
  const navigable = node.is_dir && node.has_children;

  return (
    <div
      className={cn(
        "group flex h-full items-center gap-3 border-b border-zinc-800/40 px-4",
        "row-hover",
        navigable && "cursor-pointer",
      )}
      onClick={navigable ? () => onNavigate(node.relpath) : undefined}
      role={navigable ? "button" : undefined}
      tabIndex={navigable ? 0 : undefined}
      onKeyDown={(event) => {
        if (navigable && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          onNavigate(node.relpath);
        }
      }}
    >
      {node.is_dir ? (
        <Folder className="size-4 shrink-0 text-amber-400/80" />
      ) : (
        <FileVideo className="size-4 shrink-0 text-zinc-600" />
      )}

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm text-zinc-100">{node.name}</span>
          {node.disk_count > 1 && (
            <Tooltip content={`This ${node.is_dir ? "folder" : "file"} lives on ${node.disk_count} disks`}>
              <Badge tone="amber" size="xs" className="shrink-0">
                {node.disk_count}×
              </Badge>
            </Tooltip>
          )}
          {node.dup_files > 0 && (
            <Tooltip
              content={`${count(node.dup_files, "file")} exist on more than one disk, wasting ${bytes(node.dup_wasted_bytes)}`}
            >
              <Badge tone="rose" size="xs" className="shrink-0">
                <Copy className="size-2.5" />
                {bytes(node.dup_wasted_bytes)}
              </Badge>
            </Tooltip>
          )}
        </div>
        <div className="mt-1">
          <DiskChips perDisk={node.per_disk} highlight={selectedDisk} />
        </div>
      </div>

      <div className="flex shrink-0 flex-col items-end">
        <span className="tnum text-sm text-zinc-200">{bytes(node.total_bytes)}</span>
        {node.is_dir && (
          <span className="tnum text-[11px] text-zinc-500">{count(node.total_files, "file")}</span>
        )}
      </div>

      {node.is_dir && (
        <Button
          size="sm"
          variant="outline"
          className={cn(
            "shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100",
            node.disk_count > 1 && "opacity-70",
          )}
          onClick={(event) => {
            event.stopPropagation();
            onConsolidate(node);
          }}
        >
          <Merge className="size-3.5" />
          Consolidate
        </Button>
      )}

      {navigable && <ChevronRight className="size-4 shrink-0 text-zinc-700" />}
    </div>
  );
}
