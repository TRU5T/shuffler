import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { ConflictMode, ProgressEvent, QueuePlan } from "@/lib/types";

export const keys = {
  health: ["health"] as const,
  connection: ["connection"] as const,
  disks: ["disks"] as const,
  scan: ["scan"] as const,
  scans: ["scans"] as const,
  tree: (path: string) => ["tree", path] as const,
  duplicates: (under: string, kind: string | null) => ["duplicates", under, kind] as const,
  fragmented: (under: string) => ["fragmented", under] as const,
  queue: ["queue"] as const,
  execution: ["execution"] as const,
  suggestions: (exclude: string[]) => ["suggestions", ...exclude] as const,
};

/** Everything derived from the scan index, invalidated together after a scan. */
function invalidateScanViews(client: QueryClient) {
  client.invalidateQueries({ queryKey: ["tree"] });
  client.invalidateQueries({ queryKey: ["duplicates"] });
  client.invalidateQueries({ queryKey: ["fragmented"] });
  client.invalidateQueries({ queryKey: keys.queue });
  client.invalidateQueries({ queryKey: keys.scan });
  client.invalidateQueries({ queryKey: keys.scans });
  client.invalidateQueries({ queryKey: keys.disks });
  client.invalidateQueries({ queryKey: ["suggestions"] });
}

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: api.health,
    refetchInterval: 60_000,
    retry: false,
  });
}

export function useConnection() {
  return useQuery({ queryKey: keys.connection, queryFn: api.getConnection });
}

/**
 * Polling this keeps the server's cached capacity figures fresh, which is what
 * makes the queue projections agree with the disk strip after data has moved.
 */
export function useDisks(enabled = true) {
  return useQuery({
    queryKey: keys.disks,
    queryFn: api.disks,
    enabled,
    refetchInterval: 30_000,
    retry: false,
  });
}

export function useActiveScan() {
  return useQuery({ queryKey: keys.scan, queryFn: api.activeScan });
}

export function useScanHistory() {
  return useQuery({ queryKey: keys.scans, queryFn: api.scanHistory });
}

export function useTree(path: string, enabled = true) {
  return useQuery({
    queryKey: keys.tree(path),
    queryFn: () => api.tree(path),
    enabled,
    retry: false,
    placeholderData: (previous) => previous,
  });
}

export function useDuplicates(under: string, kind: "identical" | "variant" | null, enabled = true) {
  return useQuery({
    queryKey: keys.duplicates(under, kind),
    queryFn: () => api.duplicates({ under, kind, limit: 500 }),
    enabled,
    retry: false,
  });
}

export function useFragmented(under: string, enabled = true) {
  return useQuery({
    queryKey: keys.fragmented(under),
    queryFn: () => api.fragmented(under),
    enabled,
    retry: false,
  });
}

export function useSuggestions(exclude: string[], enabled = true) {
  return useQuery({
    queryKey: keys.suggestions(exclude),
    queryFn: () => api.suggestions(exclude),
    enabled,
    retry: false,
  });
}

export function useHideSuggestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (relpath: string) => api.hideSuggestion(relpath),
    onSuccess: (data) => {
      client.setQueryData(keys.suggestions([]), data);
      client.invalidateQueries({ queryKey: ["suggestions"] });
    },
  });
}

/** Server-side preview of a candidate job, stacked on top of the current queue. */
export function usePreview(source: string | null, target: string | null) {
  return useQuery({
    queryKey: ["preview", source, target],
    queryFn: () => api.previewJob(source!, target!),
    enabled: Boolean(source !== null && target),
    retry: false,
  });
}

export function useQueuePlan() {
  return useQuery({ queryKey: keys.queue, queryFn: api.queue, retry: false });
}

export function useExecutionState() {
  return useQuery({ queryKey: keys.execution, queryFn: api.executionState, retry: false });
}

export function useScanMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (root: string) => api.scan(root),
    onSuccess: () => invalidateScanViews(client),
  });
}

export function useConnectionMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.updateConnection,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.connection });
      client.invalidateQueries({ queryKey: keys.health });
      client.invalidateQueries({ queryKey: keys.disks });
    },
  });
}

/** Queue mutations all return the recomputed plan, so we seed the cache directly. */
function useQueueMutation<TArgs>(fn: (args: TArgs) => Promise<QueuePlan>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (plan) => {
      client.setQueryData(keys.queue, plan);
      client.invalidateQueries({ queryKey: ["suggestions"] });
    },
  });
}

export function useAddJob() {
  return useQueueMutation(({ source, target }: { source: string; target: string }) =>
    api.addJob(source, target),
  );
}

export function useRemoveJob() {
  return useQueueMutation((id: string) => api.removeJob(id));
}

export function useRetryJob() {
  return useQueueMutation((id: string) => api.retryJob(id));
}

export function useClearQueue() {
  return useQueueMutation(() => api.clearQueue());
}

export function useReorderQueue() {
  return useQueueMutation((order: string[]) => api.reorderQueue(order));
}

export function useResolveConflict() {
  return useQueueMutation(
    ({
      jobId,
      relpath,
      mode,
      keepDisk,
    }: {
      jobId: string;
      relpath: string;
      mode: ConflictMode | null;
      keepDisk?: string | null;
    }) => api.resolve(jobId, relpath, mode, keepDisk),
  );
}

export function useResolveAll() {
  return useQueueMutation(
    ({
      jobId,
      mode,
      keepDisk,
      onlyUnresolved,
    }: {
      jobId: string;
      mode: ConflictMode | null;
      keepDisk?: string | null;
      onlyUnresolved?: boolean;
    }) => api.resolveAll(jobId, mode, keepDisk, onlyUnresolved),
  );
}

/**
 * Live execution feed.
 *
 * The server replays its whole log on connect, so the local buffer is cleared
 * on every (re)connection rather than appended to.
 */
export function useProgressStream() {
  const client = useQueryClient();
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const latest = useRef<ProgressEvent | null>(null);

  useEffect(() => {
    const source = new EventSource("/api/execute/events");

    source.onopen = () => {
      setConnected(true);
      setEvents([]);
      setProgress(null);
    };

    source.onerror = () => setConnected(false);

    source.onmessage = (message) => {
      let event: ProgressEvent;
      try {
        event = JSON.parse(message.data);
      } catch {
        return;
      }
      latest.current = event;

      // Per-file progress arrives several times a second and only the newest
      // one matters. Appending it would push the actual log out of the capped
      // buffer within minutes of a long run.
      if (event.type === "op_progress") {
        setProgress(event);
        return;
      }

      setProgress(event.type === "op_start" ? event : null);
      // Cap the buffer: a large queue can emit tens of thousands of events.
      setEvents((current) => {
        const next = [...current, event];
        return next.length > 1200 ? next.slice(-1200) : next;
      });

      if (event.type === "job_start" || event.type === "job_done" || event.type === "error") {
        client.invalidateQueries({ queryKey: keys.queue });
      }
      if (event.type === "queue_done") {
        client.invalidateQueries({ queryKey: keys.execution });
        client.invalidateQueries({ queryKey: keys.queue });
        client.invalidateQueries({ queryKey: keys.disks });
      }
      if (event.type === "op_done" || event.type === "queue_start") {
        client.invalidateQueries({ queryKey: keys.execution });
      }
    };

    return () => source.close();
  }, [client]);

  const clear = useCallback(() => {
    setEvents([]);
    setProgress(null);
  }, []);

  return { events, progress, connected, latest: latest.current, clear };
}

export function useExecutionControls() {
  const client = useQueryClient();
  const refresh = useCallback(() => {
    client.invalidateQueries({ queryKey: keys.execution });
    client.invalidateQueries({ queryKey: keys.queue });
  }, [client]);

  const start = useMutation({
    mutationFn: ({ dryRun, confirm }: { dryRun: boolean; confirm?: string }) =>
      api.start(dryRun, confirm),
    onSuccess: refresh,
  });
  const pause = useMutation({ mutationFn: api.pause, onSuccess: refresh });
  const resume = useMutation({ mutationFn: api.resume, onSuccess: refresh });
  const stop = useMutation({ mutationFn: api.stop, onSuccess: refresh });

  return { start, pause, resume, stop };
}
