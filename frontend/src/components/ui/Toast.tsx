import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

type Tone = "error" | "success" | "info";

interface Toast {
  id: number;
  tone: Tone;
  title: string;
  detail?: string;
}

interface ToastApi {
  error: (title: string, detail?: string) => void;
  success: (title: string, detail?: string) => void;
  info: (title: string, detail?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const ICONS = {
  error: AlertTriangle,
  success: CheckCircle2,
  info: Info,
} as const;

const TONES = {
  error: "border-rose-500/40 bg-rose-950/70 text-rose-100",
  success: "border-emerald-500/40 bg-emerald-950/70 text-emerald-100",
  info: "border-zinc-700 bg-zinc-900/90 text-zinc-100",
} as const;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (tone: Tone, title: string, detail?: string) => {
      const id = Date.now() + Math.random();
      setToasts((current) => [...current.slice(-3), { id, tone, title, detail }]);
      // Errors stay put long enough to read a full path; the rest are brief.
      window.setTimeout(() => dismiss(id), tone === "error" ? 9000 : 4000);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      error: (title, detail) => push("error", title, detail),
      success: (title, detail) => push("success", title, detail),
      info: (title, detail) => push("info", title, detail),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[80] flex w-[min(26rem,calc(100vw-2rem))] flex-col gap-2">
        {toasts.map((toast) => {
          const Icon = ICONS[toast.tone];
          return (
            <div
              key={toast.id}
              role="status"
              className={cn(
                "pointer-events-auto flex items-start gap-2.5 rounded-xl border px-3.5 py-3 shadow-xl shadow-black/50 backdrop-blur",
                TONES[toast.tone],
              )}
            >
              <Icon className="mt-0.5 size-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{toast.title}</p>
                {toast.detail && (
                  <p className="mt-0.5 break-words text-xs opacity-80">{toast.detail}</p>
                )}
              </div>
              <button
                onClick={() => dismiss(toast.id)}
                className="rounded p-0.5 opacity-60 transition-opacity hover:opacity-100"
                aria-label="Dismiss"
              >
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
