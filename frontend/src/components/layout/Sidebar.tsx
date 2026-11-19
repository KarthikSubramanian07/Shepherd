"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AudioLines,
  Boxes,
  FileText,
  GitBranch,
  LayoutDashboard,
  Network,
  Radio,
  Shield,
  ShieldAlert,
  Users,
  Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useShepherd } from "@/lib/shepherd-ws";

// `dev: true` tabs are developer/demo utilities not part of the core product
// (Voice Lab = Deepgram STT tester, Components = UI showcase). They are hidden
// unless dev mode is on · enable with `?dev=true`, disable with `?dev=false`.
const NAV = [
  { href: "/command-center", label: "Command Center", icon: LayoutDashboard },
  { href: "/fleet", label: "Fleet", icon: Users },
  { href: "/remote", label: "Remote Control", icon: Radio },
  { href: "/routines", label: "Routines", icon: GitBranch },
  { href: "/task-graph", label: "Task Graph", icon: Workflow },
  { href: "/workflows", label: "Workflows", icon: Network },
  { href: "/runs", label: "Runs", icon: Activity },
  { href: "/interventions", label: "Interventions", icon: ShieldAlert },
  { href: "/audit", label: "Audit Log", icon: FileText },
  { href: "/policy", label: "Policy", icon: Shield },
  { href: "/voice-lab", label: "Voice Lab", icon: AudioLines, dev: true },
  { href: "/kit", label: "Components", icon: Boxes, dev: true },
];

const DEV_FLAG_KEY = "shepherd:dev";

const MODES = ["LIVE", "LOCKED", "AUTONOMOUS"] as const;

export function Sidebar() {
  const pathname = usePathname();
  const isRemote = pathname.startsWith("/remote");
  const { state } = useShepherd();
  const [switching, setSwitching] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [navFocused, setNavFocused] = useState(false);
  // Expand on hover or keyboard focus so Remote's icon rail stays operable without a pointer.
  const collapsed = isRemote && !hovered && !navFocused;

  // Dev mode reveals developer-only tabs. `?dev=true` turns it on (persisted in
  // localStorage so it survives navigation, since nav links drop the query),
  // `?dev=false` turns it off. Read in an effect to avoid SSR/hydration issues.
  const [dev, setDev] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const q = new URLSearchParams(window.location.search).get("dev");
    if (q === "true") {
      window.localStorage.setItem(DEV_FLAG_KEY, "1");
      setDev(true);
    } else if (q === "false") {
      window.localStorage.removeItem(DEV_FLAG_KEY);
      setDev(false);
    } else {
      setDev(window.localStorage.getItem(DEV_FLAG_KEY) === "1");
    }
  }, [pathname]);

  const navItems = NAV.filter((item) => dev || !item.dev);

  async function switchMode(mode: string) {
    setSwitching(true);
    try {
      await api.setMode(mode);
    } catch (e) {
      console.error("mode switch failed", e);
    } finally {
      setSwitching(false);
    }
  }

  const running = state.status === "running";
  const halted = state.status === "halted";
  const watchHex = halted ? "#bb4a3a" : running ? "#cf6a43" : "#2c6e60";
  const watchLabel = halted
    ? "Halted, needs you"
    : running
      ? "Watching a run"
      : "On watch";

  return (
    <aside
      aria-label="Shepherd"
      className={cn(
        "flex shrink-0 flex-col border-r border-edge bg-panel/70 transition-all duration-200",
        isRemote ? (hovered ? "w-60" : "w-14") : "w-60",
      )}
      onMouseEnter={() => isRemote && setHovered(true)}
      onMouseLeave={() => isRemote && setHovered(false)}
      onFocus={() => isRemote && setNavFocused(true)}
      onBlur={(e) => {
        if (!isRemote) return;
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
          setNavFocused(false);
        }
      }}
    >
      {/* Brand lockup · shepherd's crook + lantern */}
      <div className={cn("flex items-center gap-2.5 pb-3 pt-4", collapsed ? "justify-center px-2" : "px-4")}>
        <ShepherdMark />
        {!collapsed && (
          <div>
            {/* Wordmark in the logo's own typeface (extracted from the brand art) */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/shepherd-wordmark.png"
              alt="Shepherd"
              className="h-[19px] w-auto"
              draggable={false}
            />
            <div className="mt-1 font-serif text-[12px] italic leading-none text-accent-ink">
              Oversight console
            </div>
          </div>
        )}
      </div>

      {/* Live watch · the agent heartbeat */}
      <div
        role="status"
        aria-live="polite"
        aria-label={`${watchLabel}, mode ${state.mode}`}
        className={cn("mx-3 mb-2 flex items-center gap-2 rounded-lg border border-edge bg-panel2/70", collapsed ? "justify-center px-1 py-2" : "px-3 py-2")}
      >
        <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
          {(running || halted) && (
            <span
              className="absolute inline-flex h-full w-full animate-watch rounded-full"
              style={{ backgroundColor: watchHex }}
            />
          )}
          <span
            className="relative inline-flex h-2 w-2 rounded-full"
            style={{ backgroundColor: watchHex }}
          />
        </span>
        {!collapsed && (
          <span className="truncate text-[11px] text-muted">
            {watchLabel} · <span className="font-mono text-ink/70">{state.mode}</span>
          </span>
        )}
      </div>

      <nav aria-label="Primary" className="flex flex-1 flex-col gap-0.5 px-2 py-1">
        {navItems.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              aria-label={collapsed ? label : undefined}
              title={collapsed ? label : undefined}
              className={cn(
                "group relative flex min-h-9 items-center rounded-lg py-2 text-sm transition-all duration-200 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ink/60",
                collapsed ? "justify-center px-1" : "gap-2.5 px-3",
                active
                  ? "bg-accent/[0.08] font-medium text-ink"
                  : "text-muted hover:translate-x-0.5 hover:bg-panel2 hover:text-ink",
              )}
            >
              {/* Lantern indicator · slides in on the active route */}
              <span
                aria-hidden="true"
                className={cn(
                  "absolute left-0 top-1/2 w-1 -translate-y-1/2 rounded-r-full bg-accent transition-all duration-300 ease-out",
                  active ? "h-5 opacity-100" : "h-0 opacity-0",
                )}
              />
              <Icon
                size={16}
                aria-hidden="true"
                className={cn(
                  "shrink-0 transition-transform duration-200",
                  active ? "scale-110 text-accent-ink" : "group-hover:scale-105",
                )}
              />
              {!collapsed && label}
            </Link>
          );
        })}
      </nav>

      {/* Mode toggle */}
      {!collapsed && (
        <div className="border-t border-edge px-3 py-3">
          <div
            id="execution-mode-label"
            className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted"
          >
            Execution mode
          </div>
          <div
            role="group"
            aria-labelledby="execution-mode-label"
            className="flex gap-1"
          >
            {MODES.map((m) => {
              const selected = state.mode === m;
              // Short label for the narrow rail; accessible name must include that
              // visible string (WCAG 2.5.3), so keep the abbreviation in the name.
              const label = m === "AUTONOMOUS" ? "AUTO" : m;
              const accessibleName =
                m === "AUTONOMOUS" ? "AUTO (autonomous)" : m;
              return (
                <button
                  key={m}
                  type="button"
                  disabled={switching || state.status === "running"}
                  aria-pressed={selected}
                  aria-label={accessibleName}
                  title={m}
                  onClick={() => void switchMode(m)}
                  className={cn(
                    "flex-1 rounded-md px-1.5 py-1.5 text-[10px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ink/60",
                    selected
                      ? "bg-accent-ink text-white shadow-card"
                      : "text-muted hover:bg-panel2 hover:text-ink",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      )}


    </aside>
  );
}

/** Shepherd brand mark · the herding-dog head from the logo, deep pine-teal.
 *  Extracted from the source artwork (transparent), so it matches the wordmark. */
function ShepherdMark() {
  return (
    <div className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-panel2 shadow-card">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/shepherd-mark.png"
        alt="Shepherd"
        className="h-7 w-7 object-contain"
        draggable={false}
      />
    </div>
  );
}
