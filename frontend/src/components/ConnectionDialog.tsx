import { CircleCheck, CircleX, KeyRound, Loader2, Plug, Server, ShieldCheck } from "lucide-react";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/Select";
import { Tooltip } from "@/components/ui/Tooltip";
import { useToast } from "@/components/ui/Toast";
import { useConnection, useConnectionMutation, useHealth } from "@/hooks/useShuffler";
import { bytes } from "@/lib/format";
import type { ConnectionSettings } from "@/lib/types";
import { cn } from "@/lib/utils";

const GB = 1024 ** 3;

export function ConnectionDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const { data: stored } = useConnection();
  const { data: health, refetch: recheck, isFetching: checking } = useHealth();
  const save = useConnectionMutation();
  const [form, setForm] = useState<Partial<ConnectionSettings>>({});
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (open && stored) {
      setForm(stored);
      setPassword("");
    }
  }, [open, stored]);

  const set = <K extends keyof ConnectionSettings>(key: K, value: ConnectionSettings[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const isSsh = form.storage_backend !== "local";

  const handleSave = async (thenTest: boolean) => {
    const patch: Partial<ConnectionSettings> = { ...form };
    delete patch.has_password;
    if (password) patch.ssh_password = password;
    else delete patch.ssh_password;

    try {
      await save.mutateAsync(patch);
      setPassword("");
      if (thenTest) {
        const result = await recheck();
        if (result.data?.ok) toast.success("Connected", result.data.message);
        else toast.error("Could not connect", result.data?.message ?? "unknown error");
      } else {
        toast.success("Settings saved");
      }
    } catch (error) {
      toast.error("Could not save settings", String(error));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="w-[min(40rem,94vw)]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Plug className="size-4 text-amber-400" />
            Connection
          </DialogTitle>
          <DialogDescription>
            Where the disks live and whether Shuffler is allowed to write.
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-5">
          <div
            className={cn(
              "flex items-start gap-2.5 rounded-xl border p-3",
              health?.ok
                ? "border-emerald-500/30 bg-emerald-500/[0.06]"
                : "border-rose-500/30 bg-rose-500/[0.06]",
            )}
          >
            {checking ? (
              <Loader2 className="mt-0.5 size-4 animate-spin text-zinc-400" />
            ) : health?.ok ? (
              <CircleCheck className="mt-0.5 size-4 shrink-0 text-emerald-400" />
            ) : (
              <CircleX className="mt-0.5 size-4 shrink-0 text-rose-400" />
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-zinc-100">
                {health?.ok ? "Connected" : "Not connected"}
              </p>
              <p className="mt-0.5 break-words text-xs text-zinc-400">{health?.message}</p>
              {health?.disks && health.disks.length > 0 && (
                <p className="mt-1 text-[11px] text-zinc-500">
                  {health.disks.map((d) => `${d.name} (${bytes(d.total)})`).join(", ")}
                </p>
              )}
            </div>
          </div>

          <Field label="Where the disks are" hint="Use SSH from this machine, or local paths when running in Docker on the Unraid host.">
            <Select
              value={form.storage_backend ?? "ssh"}
              onValueChange={(value) => {
                const backend = value as "local" | "ssh";
                setForm((current) => ({
                  ...current,
                  storage_backend: backend,
                  ...(backend === "ssh" && (current.mount_root ?? "").includes("shuffler-fixtures")
                    ? { mount_root: "/mnt" }
                    : {}),
                }));
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ssh">Remote Unraid over SSH</SelectItem>
                <SelectItem value="local">Local filesystem (bind-mounted)</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          {isSsh && (
            <div className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                <Server className="size-3.5" />
                SSH target
              </h3>
              <div className="grid gap-3 sm:grid-cols-[1fr_7rem]">
                <Field label="Host">
                  <Input
                    value={form.ssh_host ?? ""}
                    onChange={(value) => set("ssh_host", value)}
                    placeholder="tower.local or 192.168.1.10"
                  />
                </Field>
                <Field label="Port">
                  <Input
                    value={String(form.ssh_port ?? 22)}
                    onChange={(value) => set("ssh_port", Number(value) || 22)}
                    type="number"
                  />
                </Field>
              </div>
              <Field label="Username">
                <Input value={form.ssh_user ?? ""} onChange={(value) => set("ssh_user", value)} placeholder="root" />
              </Field>
              <Field
                label="Private key path"
                hint="Recommended. Leave the password blank when a key is set."
              >
                <Input
                  value={form.ssh_key_path ?? ""}
                  onChange={(value) => set("ssh_key_path", value)}
                  placeholder="~/.ssh/id_ed25519"
                />
              </Field>
              <Field
                label="Password"
                hint={
                  stored?.has_password
                    ? "A password is stored. Type to replace it, or clear the field and save to remove it."
                    : "Stored in the local database in plain text, so a key is the better choice."
                }
              >
                <div className="relative">
                  <KeyRound className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-zinc-500" />
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder={stored?.has_password ? "••••••••" : "optional"}
                    autoComplete="new-password"
                    className="h-9 w-full rounded-lg border border-zinc-700 bg-zinc-900 pl-8 pr-3 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-zinc-600"
                  />
                </div>
              </Field>
            </div>
          )}

          <Field
            label="Mount root"
            hint="The directory the numbered disks sit in. /mnt/user is never touched."
          >
            <Input
              value={form.mount_root ?? "/mnt"}
              onChange={(value) => set("mount_root", value)}
              placeholder="/mnt"
            />
          </Field>

          <div className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
              <ShieldCheck className="size-3.5" />
              Safety
            </h3>

            <label className="flex items-start gap-2.5">
              <input
                type="checkbox"
                checked={form.dry_run ?? true}
                onChange={(event) => set("dry_run", event.target.checked)}
                className="mt-0.5 size-4 accent-amber-500"
              />
              <span>
                <span className="flex items-center gap-2 text-sm text-zinc-100">
                  Default to dry runs
                  {form.dry_run && (
                    <Badge tone="emerald" size="xs">
                      recommended
                    </Badge>
                  )}
                </span>
                <span className="mt-0.5 block text-xs text-zinc-500">
                  A live run always has to be confirmed explicitly, whatever this is set to.
                </span>
              </span>
            </label>

            <Field
              label="Keep free on every disk"
              hint="Any queued job that would eat into this reserve is flagged before it runs."
            >
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  value={String(Math.round(((form.reserve_bytes ?? 0) / GB) * 10) / 10)}
                  onChange={(value) => set("reserve_bytes", Math.max(0, Number(value) || 0) * GB)}
                  className="w-28"
                />
                <span className="text-sm text-zinc-400">GB</span>
              </div>
            </Field>
          </div>
        </DialogBody>

        <DialogFooter>
          <Tooltip content="Save, then try to reach the disks">
            <Button variant="secondary" loading={save.isPending || checking} onClick={() => handleSave(true)}>
              Save and test
            </Button>
          </Tooltip>
          <Button variant="primary" loading={save.isPending} onClick={() => handleSave(false).then(onClose)}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-zinc-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] leading-relaxed text-zinc-500">{hint}</span>}
    </label>
  );
}

function Input({
  value,
  onChange,
  placeholder,
  type = "text",
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
  className?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      spellCheck={false}
      className={cn(
        "h-9 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-zinc-600",
        className,
      )}
    />
  );
}
