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
  Mic,
  Radio,
  Shield,
  ShieldAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useShepherd } from "@/lib/shepherd-ws";
import { Button } from "@/components/ui/primitives";

// `dev: true` tabs are developer/demo utilities not part of the core product
// (Voice Lab = Deepgram STT tester, Components = UI showcase). They are hidden
// unless dev mode is on — enable with `?dev=true`, disable with `?dev=false`.
const NAV = [
  { href: "/command-center", label: "Command Center", icon: LayoutDashboard },
  { href: "/remote", label: "Remote Control", icon: Radio },
  { href: "/routines", label: "Routines", icon: GitBranch },
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
  const { state } = useShepherd();
  const [switching, setSwitching] = useState(false);

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

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-edge bg-panel/60">
      <div className="flex items-center gap-2 px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/15 text-accent">
          <GitBranch size={18} />
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight">Shepherd</div>
          <div className="text-[11px] text-muted">Agent Command Center</div>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-2 py-2">
        {navItems.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-accent/10 text-accent"
                  : "text-muted hover:bg-panel2 hover:text-ink",
              )}
            >
              <Icon size={16} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Mode toggle */}
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
                "flex-1 rounded-md px-1.5 py-1 text-[10px] font-medium transition-colors",
                state.mode === m
                  ? "bg-accent/20 text-accent"
                  : "text-muted hover:bg-panel2 hover:text-ink",
              )}
            >
              {m.slice(0, m === "AUTONOMOUS" ? 4 : m.length)}
            </button>
          ))}
        </div>
      </div>

      <div className="border-t border-edge p-3">
        <Button className="w-full" variant="primary">
          <Mic size={15} />
          Record new tool
        </Button>
        <p className="mt-2 text-center text-[10px] text-muted">
          Demonstrate a task once — agent runs it after.
        </p>
      </div>
    </aside>
  );
}
