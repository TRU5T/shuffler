// Mirrors backend/app/models.py.

export interface Disk {
  name: string;
  path: string;
  total: number;
  used: number;
  free: number;
}

export interface DiskUsage {
  bytes: number;
  files: number;
}

export interface TreeNode {
  name: string;
  relpath: string;
  is_dir: boolean;
  /** Bytes across every disk, counting each copy of a duplicate. */
  total_bytes: number;
  /** Files on disk, counting each copy, so it matches total_bytes. */
  total_files: number;
  /** Distinct relative paths: a file on three disks counts once. */
  distinct_files: number;
  per_disk: Record<string, DiskUsage>;
  disk_count: number;
  dup_files: number;
  dup_wasted_bytes: number;
  has_children: boolean;
}

export type DuplicateKind = "identical" | "variant";

export interface DuplicateGroup {
  relpath: string;
  kind: DuplicateKind;
  copies: Record<string, number>;
  wasted_bytes: number;
}

export interface ScanSummary {
  id: string;
  root: string;
  created_at: number;
  disks: Disk[];
  total_files: number;
  distinct_files: number;
  total_bytes: number;
  dup_files: number;
  dup_wasted_bytes: number;
  duration_seconds: number;
}

export interface ScanState {
  scanning: boolean;
  scan: ScanSummary | null;
}

export interface TreeResponse {
  node: TreeNode;
  children: TreeNode[];
  breadcrumbs: TreeNode[];
}

export interface DuplicatesResponse {
  groups: DuplicateGroup[];
  total_groups: number;
  total_wasted_bytes: number;
}

export type ConflictMode = "keep_larger" | "keep_smaller" | "keep_disk" | "keep_both" | "skip";

export interface Conflict {
  relpath: string;
  copies: Record<string, number>;
  kind: DuplicateKind;
  mode: ConflictMode | null;
  keep_disk: string | null;
  resolved: boolean;
  survivor: string | null;
}

export type JobStatus = "draft" | "ready" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  source_relpath: string;
  target_disk: string;
  status: JobStatus;
  conflicts: Conflict[];
  move_bytes: number;
  reclaim_bytes: number;
  move_files: number;
  delete_files: number;
  position: number;
  created_at: number;
  error: string | null;
  bytes_done: number;
  unresolved_conflicts: number;
}

export interface DiskProjection {
  name: string;
  total: number;
  used_before: number;
  free_before: number;
  used_after: number;
  free_after: number;
  delta: number;
  overflow: boolean;
}

export interface JobPlan {
  job: Job;
  disks_after: Record<string, DiskProjection>;
  overflow: boolean;
  blocked_reason: string | null;
}

export interface QueuePlan {
  disks_now: Disk[];
  jobs: JobPlan[];
  final: Record<string, DiskProjection>;
  total_move_bytes: number;
  total_reclaim_bytes: number;
  ready: boolean;
  blocked_reasons: string[];
}

export interface Suggestion {
  source_relpath: string;
  source_name: string;
  from_disk: string;
  target_disk: string;
  move_bytes: number;
  move_files: number;
  disks_after: Record<string, DiskProjection>;
  spread_improvement: number;
}

export interface SuggestionSet {
  balanced: boolean;
  headline: string;
  spread_bytes: number;
  mean_free_bytes: number;
  suggestions: Suggestion[];
}

export interface PreviewResponse {
  source_relpath: string;
  target_disk: string;
  conflicts: Conflict[];
  move_bytes: number;
  reclaim_bytes: number;
  move_files: number;
  delete_files: number;
  disks_after: Record<string, DiskProjection>;
  overflow: boolean;
  blocked_reason: string | null;
}

export type OpKind = "move" | "delete" | "prune";

export interface Operation {
  kind: OpKind;
  src: string;
  dst: string | null;
  size: number;
  relpath: string;
  renamed: boolean;
}

export type ProgressEventType =
  | "queue_start"
  | "job_start"
  | "op_start"
  | "op_progress"
  | "op_done"
  | "job_done"
  | "queue_done"
  | "log"
  | "error";

export interface ProgressEvent {
  type: ProgressEventType;
  ts: number;
  job_id: string | null;
  relpath: string | null;
  message: string | null;
  bytes_done: number;
  bytes_total: number;
  ops_done: number;
  ops_total: number;
  dry_run: boolean;
  file_bytes_done: number;
  file_bytes_total: number;
  bytes_per_second: number;
  eta_seconds: number | null;
}

export interface ExecutionState {
  running: boolean;
  paused: boolean;
  dry_run: boolean;
  current_job: string | null;
  ops_done: number;
  ops_total: number;
  bytes_done: number;
  bytes_total: number;
  started_at: number | null;
  finished_at: number | null;
  last_error: string | null;
  current_file: string | null;
  file_bytes_done: number;
  file_bytes_total: number;
  bytes_per_second: number;
  eta_seconds: number | null;
}

export interface ConnectionSettings {
  storage_backend: "local" | "ssh";
  ssh_host: string;
  ssh_port: number;
  ssh_user: string;
  ssh_key_path: string;
  ssh_password: string | null;
  mount_root: string;
  dry_run: boolean;
  reserve_bytes: number;
  has_password: boolean;
}

export interface HealthResult {
  ok: boolean;
  backend: string;
  dry_run: boolean;
  message: string;
  disks: Disk[];
}
