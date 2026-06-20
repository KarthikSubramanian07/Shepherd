"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AudioLines,
  Boxes,
  GitBranch,
  LayoutDashboard,
  Mic,
  Radio,
  ShieldAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/primitives";

const NAV = [
  { href: "/command-center", label: "Command Center", icon: LayoutDashboard },
  { href: "/remote", label: "Remote Control", icon: Radio },
  { href: "/routines", label: "Routines", icon: GitBranch },
  { href: "/runs", label: "Runs", icon: Activity },
  { href: "/interventions", label: "Interventions", icon: ShieldAlert },
  { href: "/voice-lab", label: "Voice Lab", icon: AudioLines },
  { href: "/kit", label: "Components", icon: Boxes },
];

export function Sidebar() {
  const pathname = usePathname();

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
        {NAV.map(({ href, label, icon: Icon }) => {
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

      <div className="border-t border-edge p-3">
        <Button className="w-full" variant="primary">
          <Mic size={15} />
          Record new tool
        </Button>
        <p className="mt-2 text-center text-[10px] text-muted">
          Monkey see, monkey do — record &amp; narrate a task.
        </p>
      </div>
    </aside>
  );
}
