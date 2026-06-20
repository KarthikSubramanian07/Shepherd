import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDuration(ms?: number): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s % 60)}s`;
}

export function timeAgo(iso?: string): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

/**
 * Generates an inline SVG "screenshot" placeholder as a data URI so the mock
 * works fully offline (no remote image dependency). Swap for real screenshot
 * URLs once the backend serves them.
 */
export function placeholderShot(label: string, tone: "neutral" | "ok" | "flag" | "halt" = "neutral"): string {
  const bg = { neutral: "#161b27", ok: "#0e2a1a", flag: "#2a230e", halt: "#2a1414" }[tone];
  const bar = { neutral: "#3b82f6", ok: "#22c55e", flag: "#f59e0b", halt: "#ef4444" }[tone];
  const safe = label.replace(/[<>&]/g, "").slice(0, 28);
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='320' height='180'>
    <rect width='320' height='180' fill='${bg}'/>
    <rect x='0' y='0' width='320' height='26' fill='#0b0e14'/>
    <circle cx='14' cy='13' r='4' fill='#ef4444'/>
    <circle cx='30' cy='13' r='4' fill='#f59e0b'/>
    <circle cx='46' cy='13' r='4' fill='#22c55e'/>
    <rect x='70' y='8' width='230' height='11' rx='5' fill='#222a39'/>
    <rect x='16' y='44' width='${bar === "#3b82f6" ? 160 : 200}' height='10' rx='4' fill='${bar}' opacity='0.85'/>
    <rect x='16' y='66' width='250' height='8' rx='4' fill='#2b3445'/>
    <rect x='16' y='84' width='210' height='8' rx='4' fill='#2b3445'/>
    <rect x='16' y='110' width='120' height='28' rx='6' fill='${bar}' opacity='0.25' stroke='${bar}'/>
    <text x='24' y='128' font-family='monospace' font-size='11' fill='${bar}'>${safe}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}
