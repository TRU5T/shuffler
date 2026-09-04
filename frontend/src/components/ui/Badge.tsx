import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badge = cva(
  "inline-flex items-center gap-1 rounded-md border font-medium whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "border-zinc-700/70 bg-zinc-800/60 text-zinc-300",
        amber: "border-amber-500/30 bg-amber-500/10 text-amber-300",
        rose: "border-rose-500/30 bg-rose-500/10 text-rose-300",
        emerald: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
        sky: "border-sky-500/30 bg-sky-500/10 text-sky-300",
        violet: "border-violet-500/30 bg-violet-500/10 text-violet-300",
      },
      size: {
        xs: "px-1.5 py-px text-[10px]",
        sm: "px-2 py-0.5 text-xs",
      },
    },
    defaultVariants: { tone: "neutral", size: "sm" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badge> {}

export function Badge({ className, tone, size, ...props }: BadgeProps) {
  return <span className={cn(badge({ tone, size }), className)} {...props} />;
}
