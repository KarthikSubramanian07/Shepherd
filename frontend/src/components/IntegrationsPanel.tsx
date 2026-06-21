"use client";

import { useEffect, useState } from "react";
import { api, type Integration } from "@/lib/api";

const DOT: Record<Integration["status"], string> = {
  active: "bg-ok",
  ready: "bg-accent",
  off: "bg-muted/50",
};

const LABEL: Record<Integration["status"], string> = {
  active: "active",
  ready: "ready",
  off: "off",
};

/**
 * Live status of every integration so nothing the system does is invisible:
 * active (running now), ready (built + keyed, fires when triggered), or off
 * (graceful fallback). Polls a few seconds so a judge sees it light up live.
 */
export function IntegrationsPanel() {
  const [items, setItems] = useState<Integration[] | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .getIntegrations()
        .then((r) => alive && setItems(r.integrations))
        .catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (!items || items.length === 0) return null;

  const active = items.filter((i) => i.status === "active").length;

  return (
    <div className="rounded-xl border border-edge bg-panel/80 p-5" role="status" aria-live="polite">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">Integrations</h2>
        <span className="text-[11px] text-muted">
          {active}/{items.length} active
        </span>
      </div>
      <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {items.map((it) => (
          <li
            key={it.name}
            className="flex items-start gap-2 rounded-lg border border-edge/60 bg-canvas/40 px-3 py-2"
            title={it.detail}
          >
            <span
              aria-hidden="true"
              className={`mt-1 h-2 w-2 shrink-0 rounded-full ${DOT[it.status]} ${
                it.status === "active" ? "animate-pulseRing motion-reduce:animate-none" : ""
              }`}
            />
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="truncate text-xs font-medium text-ink">{it.name}</span>
                <span className="text-[9px] uppercase tracking-wide text-muted">
                  {LABEL[it.status]}
                </span>
              </div>
              <div className="truncate text-[11px] text-muted">{it.detail}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
