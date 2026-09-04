import type {
  ConnectionSettings,
  Disk,
  DuplicateGroup,
  DuplicatesResponse,
  DuplicateKind,
  ExecutionState,
  HealthResult,
  Job,
  Operation,
  PreviewResponse,
  ProgressEvent,
  QueuePlan,
  ScanState,
  ScanSummary,
  TreeNode,
  TreeResponse,
  ConflictMode,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = body.detail.map((d: any) => d.msg).join("; ");
    } catch {
      // Keep the status-line fallback.
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<HealthResult>("/health"),
  testConnection: () => post<HealthResult>("/connection/test"),
  getConnection: () => request<ConnectionSettings>("/connection"),
  updateConnection: (patch: Partial<ConnectionSettings>) =>
    request<ConnectionSettings>("/connection", { method: "PUT", body: JSON.stringify(patch) }),
  disks: () => request<Disk[]>("/disks"),

  scan: (root: string) => post<ScanSummary>("/scan", { root }),
  activeScan: () => request<ScanState>("/scan"),
  scanHistory: () => request<ScanSummary[]>("/scans"),
  activateScan: (id: string) => post<ScanSummary>(`/scans/${id}/activate`),

  tree: (path: string) => request<TreeResponse>(`/tree?path=${encodeURIComponent(path)}`),
  duplicates: (params: {
    under?: string;
    kind?: DuplicateKind | null;
    limit?: number;
    offset?: number;
  }) => {
    const query = new URLSearchParams();
    if (params.under) query.set("under", params.under);
    if (params.kind) query.set("kind", params.kind);
    query.set("limit", String(params.limit ?? 200));
    query.set("offset", String(params.offset ?? 0));
    return request<DuplicatesResponse>(`/duplicates?${query}`);
  },
  fragmented: (under = "") =>
    request<TreeNode[]>(`/fragmented?under=${encodeURIComponent(under)}`),

  queue: () => request<QueuePlan>("/queue"),
  previewJob: (source_relpath: string, target_disk: string) =>
    post<PreviewResponse>("/queue/preview", { source_relpath, target_disk }),
  addJob: (source_relpath: string, target_disk: string) =>
    post<QueuePlan>("/queue", { source_relpath, target_disk }),
  removeJob: (id: string) => request<QueuePlan>(`/queue/${id}`, { method: "DELETE" }),
  retryJob: (id: string) => post<QueuePlan>(`/queue/${id}/retry`, {}),
  clearQueue: () => request<QueuePlan>("/queue", { method: "DELETE" }),
  reorderQueue: (order: string[]) => post<QueuePlan>("/queue/reorder", { order }),
  job: (id: string) => request<Job>(`/queue/${id}`),
  jobOperations: (id: string) => request<Operation[]>(`/queue/${id}/operations`),
  resolve: (id: string, relpath: string, mode: ConflictMode | null, keepDisk?: string | null) =>
    post<QueuePlan>(`/queue/${id}/resolve`, { relpath, mode, keep_disk: keepDisk ?? null }),
  resolveAll: (
    id: string,
    mode: ConflictMode | null,
    keepDisk?: string | null,
    onlyUnresolved = false,
  ) =>
    post<QueuePlan>(`/queue/${id}/resolve-all`, {
      mode,
      keep_disk: keepDisk ?? null,
      only_unresolved: onlyUnresolved,
    }),

  executionState: () => request<ExecutionState>("/execute/state"),
  executionLog: () => request<ProgressEvent[]>("/execute/log"),
  start: (dryRun: boolean, confirm?: string) =>
    post<{ started: boolean; message: string; state: ExecutionState }>("/execute/start", {
      dry_run: dryRun,
      confirm: confirm ?? null,
    }),
  pause: () => post<ExecutionState>("/execute/pause"),
  resume: () => post<ExecutionState>("/execute/resume"),
  stop: () => post<ExecutionState>("/execute/stop"),
};

export type { DuplicateGroup };
