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
  const collapsed = isRemote && !hovered;

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
      className={cn(
        "flex shrink-0 flex-col border-r border-edge bg-panel/70 transition-all duration-200",
        isRemote ? (hovered ? "w-60" : "w-14") : "w-60",
      )}
      onMouseEnter={() => isRemote && setHovered(true)}
      onMouseLeave={() => isRemote && setHovered(false)}
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
      <div className={cn("mx-3 mb-2 flex items-center gap-2 rounded-lg border border-edge bg-panel2/70", collapsed ? "justify-center px-1 py-2" : "px-3 py-2")}>
        <span className="relative flex h-2 w-2 shrink-0">
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

      <nav className="flex flex-1 flex-col gap-0.5 px-2 py-1">
        {navItems.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={cn(
                "group relative flex items-center rounded-lg py-2 text-sm transition-all duration-200 ease-out",
                collapsed ? "justify-center px-1" : "gap-2.5 px-3",
                active
                  ? "bg-accent/[0.08] font-medium text-ink"
                  : "text-muted hover:translate-x-0.5 hover:bg-panel2 hover:text-ink",
              )}
            >
              {/* Lantern indicator · slides in on the active route */}
              <span
                className={cn(
                  "absolute left-0 top-1/2 w-1 -translate-y-1/2 rounded-r-full bg-accent transition-all duration-300 ease-out",
                  active ? "h-5 opacity-100" : "h-0 opacity-0",
                )}
              />
              <Icon
                size={16}
                className={cn(
                  "shrink-0 transition-transform duration-200",
                  active ? "scale-110 text-accent" : "group-hover:scale-105",
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
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted">
            Execution mode
          </div>
          <div className="flex gap-1">
            {MODES.map((m) => (
              <button
                key={m}
                disabled={switching || state.status === "running"}
                onClick={() => void switchMode(m)}
                className={cn(
                  "flex-1 rounded-md px-1.5 py-1 text-[10px] font-semibold transition-colors",
                  state.mode === m
                    ? "bg-accent text-white shadow-card"
                    : "text-muted hover:bg-panel2 hover:text-ink",
                )}
              >
                {m.slice(0, m === "AUTONOMOUS" ? 4 : m.length)}
              </button>
            ))}
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
