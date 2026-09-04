import { FlaskConical, Play, ShieldAlert, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { useExecutionControls } from "@/hooks/useShuffler";
import { bytes, count } from "@/lib/format";
import type { QueuePlan } from "@/lib/types";
import { cn } from "@/lib/utils";

const CONFIRM_PHRASE = "MOVE MY FILES";

/**
 * The gate between planning and touching data.
 *
 * A dry run is one click. A live run needs the confirmation phrase typed out,
 * because it deletes source files after copying them.
 */
export function RunDialog({
  open,
  plan,
  onClose,
}: {
  open: boolean;
  plan: QueuePlan | undefined;
  onClose: () => void;
}) {
  const toast = useToast();
  const { start } = useExecutionControls();
  const [live, setLive] = useState(false);
  const [phrase, setPhrase] = useState("");

  useEffect(() => {
    if (open) {
      setLive(false);
      setPhrase("");
    }
  }, [open]);

  const runnable = (plan?.jobs ?? []).filter((jobPlan) => jobPlan.job.status === "ready");
  const blocked = plan?.blocked_reasons ?? [];
  const totalMove = runnable.reduce((sum, j) => sum + j.job.move_bytes, 0);
  const totalReclaim = runnable.reduce((sum, j) => sum + j.job.reclaim_bytes, 0);
  const totalFiles = runnable.reduce((sum, j) => sum + j.job.move_files, 0);
  const totalDeletes = runnable.reduce((sum, j) => sum + j.job.delete_files, 0);

  const canRun = runnable.length > 0 && (!live || phrase.trim() === CONFIRM_PHRASE);

  const handleRun = () => {
    start.mutate(
      { dryRun: !live, confirm: live ? CONFIRM_PHRASE : undefined },
      {
        onSuccess: (result) => {
          if (result.started) {
            toast.success(live ? "Queue started" : "Dry run started", result.message);
            onClose();
          } else {
            toast.error("Nothing was started", result.message);
          }
        },
        onError: (error) => toast.error("Could not start", String(error)),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="w-[min(38rem,94vw)]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Play className="size-4 text-amber-400" />
            Run the queue
          </DialogTitle>
          <DialogDescription>
            {runnable.length > 0
              ? `${count(runnable.length, "job")} ready.`
              : "No jobs are ready yet."}
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-4">
          {blocked.length > 0 && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.06] p-3">
              <p className="mb-1.5 flex items-center gap-2 text-sm text-amber-200">
                <TriangleAlert className="size-4" />
                {count(blocked.length, "job")} will be skipped
              </p>
              <ul className="space-y-0.5 text-xs text-amber-200/70">
                {blocked.map((reason) => (
                  <li key={reason} className="break-words">
                    {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <Summary label="Files to relocate" value={totalFiles.toLocaleString()} sub={bytes(totalMove)} />
            <Summary
              label="Copies to delete"
              value={totalDeletes.toLocaleString()}
              sub={`${bytes(totalReclaim)} reclaimed`}
              tone={totalDeletes > 0 ? "rose" : undefined}
            />
          </div>

          <fieldset className="space-y-2">
            <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
              Mode
            </legend>

            <ModeOption
              selected={!live}
              onSelect={() => setLive(false)}
              icon={<FlaskConical className="size-4" />}
              title="Dry run"
              badge={<Badge tone="emerald" size="xs">safe</Badge>}
              description="Walks the entire queue and reports every operation it would perform. Nothing is copied, deleted or renamed."
            />

            <ModeOption
              selected={live}
              onSelect={() => setLive(true)}
              icon={<ShieldAlert className="size-4" />}
              title="Live run"
              badge={<Badge tone="rose" size="xs">writes data</Badge>}
              description="Copies each file to its target disk, verifies the destination size, then deletes the source and prunes the empty folders left behind."
              danger
            />
          </fieldset>

          {live && (
            <div className="rounded-xl border border-rose-500/30 bg-rose-500/[0.06] p-3">
              <p className="text-sm text-rose-100">
                This deletes files. Type{" "}
                <code className="rounded bg-rose-950/60 px-1.5 py-0.5 font-mono text-xs text-rose-200">
                  {CONFIRM_PHRASE}
                </code>{" "}
                to confirm.
              </p>
              <input
                value={phrase}
                onChange={(event) => setPhrase(event.target.value)}
                placeholder={CONFIRM_PHRASE}
                autoComplete="off"
                spellCheck={false}
                className="mt-2 h-9 w-full rounded-lg border border-rose-500/40 bg-zinc-950 px-3 font-mono text-sm text-rose-100 placeholder:text-rose-500/40 focus:border-rose-400"
              />
              <p className="mt-2 text-[11px] text-rose-200/60">
                Transfers address disks directly and never route through /mnt/user. You can pause or
                stop at any point; a file is only removed once its copy has been verified.
              </p>
            </div>
          )}
        </DialogBody>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={live ? "danger" : "primary"}
            disabled={!canRun}
            loading={start.isPending}
            onClick={handleRun}
          >
            {live ? "Start live run" : "Start dry run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Summary({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "rose";
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p
        className={cn(
          "tnum mt-0.5 text-xl font-semibold",
          tone === "rose" ? "text-rose-300" : "text-zinc-100",
        )}
      >
        {value}
      </p>
      <p className="tnum text-[11px] text-zinc-500">{sub}</p>
    </div>
  );
}

function ModeOption({
  selected,
  onSelect,
  icon,
  title,
  badge,
  description,
  danger,
}: {
  selected: boolean;
  onSelect: () => void;
  icon: React.ReactNode;
  title: string;
  badge: React.ReactNode;
  description: string;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-3 rounded-xl border p-3 text-left transition-all",
        selected
          ? danger
            ? "border-rose-500/50 bg-rose-500/[0.07] ring-1 ring-rose-500/25"
            : "border-emerald-500/50 bg-emerald-500/[0.07] ring-1 ring-emerald-500/25"
          : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700",
      )}
    >
      <span className={cn("mt-0.5", selected ? (danger ? "text-rose-300" : "text-emerald-300") : "text-zinc-500")}>
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="text-sm font-medium text-zinc-100">{title}</span>
          {badge}
        </span>
        <span className="mt-0.5 block text-xs leading-relaxed text-zinc-400">{description}</span>
      </span>
    </button>
  );
}
