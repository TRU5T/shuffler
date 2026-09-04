const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

/** Human-readable byte count. Media libraries live in GB/TB, so keep it terse. */
export function bytes(value: number, digits?: number): string {
  const sign = value < 0 ? "-" : "";
  let n = Math.abs(value);
  let unit = 0;
  while (n >= 1024 && unit < UNITS.length - 1) {
    n /= 1024;
    unit += 1;
  }
  const precision = digits ?? (unit === 0 ? 0 : n < 10 ? 2 : n < 100 ? 1 : 0);
  return `${sign}${n.toFixed(precision)} ${UNITS[unit]}`;
}

/** Signed byte delta, with an explicit + so gains and losses read differently. */
export function delta(value: number): string {
  if (value === 0) return "no change";
  return `${value > 0 ? "+" : "−"}${bytes(Math.abs(value))}`;
}

export function count(n: number, singular: string, plural?: string): string {
  return `${n.toLocaleString()} ${n === 1 ? singular : (plural ?? `${singular}s`)}`;
}

export function percent(value: number, total: number): number {
  if (!total) return 0;
  return Math.min(100, Math.max(0, (value / total) * 100));
}

export function relativeTime(epochSeconds: number): string {
  const seconds = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (seconds < 45) return "just now";
  if (seconds < 90) return "a minute ago";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function duration(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

/** Transfer rate. Below a KB/s it is noise, so it reads as a stall. */
export function rate(bytesPerSecond: number): string {
  if (!bytesPerSecond || bytesPerSecond < 1024) return "—";
  return `${bytes(bytesPerSecond)}/s`;
}

/** Coarse remaining time. Precision here would only ever be wrong. */
export function eta(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return `${Math.round(seconds)}s left`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m left`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m left`;
}

export function clockTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Split a relative path into a directory prefix and the final segment. */
export function splitPath(relpath: string): { dir: string; name: string } {
  const index = relpath.lastIndexOf("/");
  if (index === -1) return { dir: "", name: relpath };
  return { dir: relpath.slice(0, index + 1), name: relpath.slice(index + 1) };
}
