import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Stable colour per disk.
 *
 * Disk identity is the primary visual language of this app: the same disk must
 * be the same colour in the disk strip, the tree chips and the queue
 * projections, so the eye can track it without reading labels.
 */
const PALETTE = [
  { text: "text-sky-300", bg: "bg-sky-500", ring: "ring-sky-500/40", soft: "bg-sky-500/15" },
  { text: "text-amber-300", bg: "bg-amber-500", ring: "ring-amber-500/40", soft: "bg-amber-500/15" },
  {
    text: "text-emerald-300",
    bg: "bg-emerald-500",
    ring: "ring-emerald-500/40",
    soft: "bg-emerald-500/15",
  },
  {
    text: "text-violet-300",
    bg: "bg-violet-500",
    ring: "ring-violet-500/40",
    soft: "bg-violet-500/15",
  },
  { text: "text-rose-300", bg: "bg-rose-500", ring: "ring-rose-500/40", soft: "bg-rose-500/15" },
  { text: "text-teal-300", bg: "bg-teal-500", ring: "ring-teal-500/40", soft: "bg-teal-500/15" },
  {
    text: "text-fuchsia-300",
    bg: "bg-fuchsia-500",
    ring: "ring-fuchsia-500/40",
    soft: "bg-fuchsia-500/15",
  },
  { text: "text-lime-300", bg: "bg-lime-500", ring: "ring-lime-500/40", soft: "bg-lime-500/15" },
];

export function diskColor(name: string) {
  const digits = name.match(/\d+/);
  const index = digits ? Number(digits[0]) - 1 : name.length;
  return PALETTE[((index % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

export function sortDiskNames(names: string[]): string[] {
  return [...names].sort((a, b) => {
    const na = Number(a.match(/\d+/)?.[0] ?? Number.MAX_SAFE_INTEGER);
    const nb = Number(b.match(/\d+/)?.[0] ?? Number.MAX_SAFE_INTEGER);
    return na === nb ? a.localeCompare(b) : na - nb;
  });
}
