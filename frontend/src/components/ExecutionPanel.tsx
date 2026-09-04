import {
  CircleCheck,
  CircleX,
  FileDown,
  FlaskConical,
  Pause,
  Play,
  Radio,
  Square,
  Trash2,
} from "lucide-react";
import { useEffect, useRef } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { useExecutionControls } from "@/hooks/useShuffler";
import { bytes, clockTime, eta, percent, rate, splitPath } from "@/lib/format";
import type { ExecutionState, ProgressEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

const EVENT_STYLES: Partial<Record<ProgressEvent["type"], string>> = {
  error: "text-rose-300",
  job_start: "text-sky-300",
  job_done: "text-emerald-300",
  queue_start: "text-amber-300",
  queue_done: "text-amber-300",
  log: "text-zinc-400",
};

/** Live progress while the queue runs, and the log once it has finished. */
export function ExecutionPanel({
  state,
  events,
  progress,
  connected,
  onClearLog,
}: {
  state: ExecutionState | undefined;
  events: ProgressEvent[];
  progress: ProgressEvent | null;
  connected: boolean;
  onClearLog: () => void;
}) {
  const toast = useToast();
  const { pause, resume, stop } = useExecutionControls();
  const logRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  // Follow the tail unless the user has scrolled up to read something.
  useEffect(() => {
    const element = logRef.current;
    if (element && pinnedToBottom.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [events.length]);

  const visible = events.filter(
    (event) => event.type !== "op_start" && event.type !== "op_done",
  );
  const running = state?.running ?? false;
  const overall = state ? percent(state.bytes_done, state.bytes_total || 1) : 0;
  const finished = !running && (state?.ops_done ?? 0) > 0;

  // The event stream is more current than the polled state, so prefer it for
  // the per-file figures and fall back to the state between events.
  const currentFile = progress?.relpath ?? state?.current_file ?? null;
  const fileDone = progress?.file_bytes_done ?? state?.file_bytes_done ?? 0;
  const fileTotal = progress?.file_bytes_total ?? state?.file_bytes_total ?? 0;
  const speed = progress?.bytes_per_second ?? state?.bytes_per_second ?? 0;
  const remaining = eta(progress?.eta_seconds ?? state?.eta_seconds);

  if (!running && events.length === 0) return null;

  return (
    <section className="panel flex min-h-0 flex-col">
      <header className="panel-header">
        <div className="flex items-center gap-2">
          {running ? (
            <Radio className="size-4 animate-pulse text-sky-400" />
          ) : state?.last_error ? (
            <CircleX className="size-4 text-rose-400" />
          ) : (
            <CircleCheck className="size-4 text-emerald-400" />
          )}
          <h2 className="text-sm font-semibold text-zinc-200">
            {running ? (state?.paused ? "Paused" : "Running") : "Last run"}
          </h2>
          {state?.dry_run && (
            <Badge tone="emerald" size="xs">
              <FlaskConical className="size-2.5" />
              dry run
            </Badge>
          )}
          {!connected && running && (
            <Badge tone="amber" size="xs">
              reconnecting
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {running &&
            (state?.paused ? (
              <Button
                size="sm"
                variant="secondary"
                onClick={() =>
                  resume.mutate(undefined, {
                    onError: (error) => toast.error("Could not resume", String(error)),
                  })
                }
              >
                <Play className="size-3.5" />
                Resume
              </Button>
            ) : (
              <Button
                size="sm"
                variant="secondary"
                onClick={() =>
                  pause.mutate(undefined, {
                    onError: (error) => toast.error("Could not pause", String(error)),
                  })
                }
              >
                <Pause className="size-3.5" />
                Pause
              </Button>
            ))}
          {running && (
            <Button
              size="sm"
              variant="danger"
              onClick={() =>
                stop.mutate(undefined, {
                  onError: (error) => toast.error("Could not stop", String(error)),
                })
              }
            >
              <Square className="size-3.5" />
              Stop
            </Button>
          )}
          {finished && (
            <Button size="sm" variant="ghost" onClick={onClearLog}>
              <Trash2 className="size-3.5" />
              Clear log
            </Button>
          )}
        </div>
      </header>

      {state && (
        <div className="space-y-3 border-b border-zinc-800/80 px-4 py-3">
          <div>
            <div className="mb-1.5 flex items-baseline justify-between gap-3 text-xs">
              <span className="tnum shrink-0 text-zinc-400">
                {state.ops_done.toLocaleString()} / {state.ops_total.toLocaleString()} operations
              </span>
              <span className="tnum flex shrink-0 items-baseline gap-2">
                {running && speed > 0 && (
                  <span className="font-medium text-sky-300">{rate(speed)}</span>
                )}
                {running && remaining && <span className="text-zinc-500">{remaining}</span>}
                <span className="text-zinc-400">
                  {bytes(state.bytes_done)} / {bytes(state.bytes_total)}
                </span>
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
              <div
                className={cn(
                  "h-full rounded-full transition-[width] duration-300",
                  state.dry_run
                    ? "bg-emerald-500"
                    : running
                      ? "stripes-animated bg-sky-500"
                      : "bg-sky-500",
                )}
                style={{ width: `${overall}%` }}
              />
            </div>
          </div>

          {running && currentFile && (
            <CurrentFile
              relpath={currentFile}
              done={fileDone}
              total={fileTotal}
              dryRun={state.dry_run}
            />
          )}

          {state.last_error && (
            <p className="mt-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-200">
              {state.last_error}
            </p>
          )}
        </div>
      )}

      <div
        ref={logRef}
        onScroll={(event) => {
          const element = event.currentTarget;
          pinnedToBottom.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 40;
        }}
        className="scroll-slim min-h-0 flex-1 overflow-y-auto px-4 py-2 font-mono text-[11px] leading-relaxed"
      >
        {visible.map((event, index) => (
          <div key={`${event.ts}-${index}`} className="flex gap-2">
            <span className="shrink-0 text-zinc-700">{clockTime(event.ts)}</span>
            <span className={cn("min-w-0 break-all", EVENT_STYLES[event.type] ?? "text-zinc-300")}>
              {event.message ?? event.relpath ?? event.type}
            </span>
          </div>
        ))}
        {visible.length === 0 && <p className="py-4 text-center text-zinc-600">Waiting…</p>}
      </div>
    </section>
  );
}

/** The file being copied right now, with its own progress bar. */
function CurrentFile({
  relpath,
  done,
  total,
  dryRun,
}: {
  relpath: string;
  done: number;
  total: number;
  dryRun: boolean;
}) {
  const { dir, name } = splitPath(relpath);
  const progress = percent(done, total || 1);
  // A file whose size is not known yet would otherwise render an empty bar with
  // a misleading "0 B / 0 B" beside it.
  const measured = total > 0;

  return (
    <div className="rounded-lg border border-zinc-800/70 bg-zinc-900/40 px-2.5 py-2">
      <div className="mb-1.5 flex items-baseline justify-between gap-3 text-[11px]">
        <span className="flex min-w-0 items-baseline gap-1.5">
          <FileDown className="size-3 shrink-0 translate-y-0.5 text-sky-400" />
          <span className="min-w-0 truncate" title={relpath}>
            {dir && <span className="text-zinc-600">{dir}</span>}
            <span className="text-zinc-300">{name}</span>
          </span>
        </span>
        {measured && (
          <span className="tnum shrink-0 text-zinc-500">
            {bytes(done)} / {bytes(total)}
          </span>
        )}
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-300",
            dryRun ? "bg-emerald-400/70" : "bg-sky-400",
          )}
          style={{ width: `${measured ? progress : 0}%` }}
        />
      </div>
    </div>
  );
}
