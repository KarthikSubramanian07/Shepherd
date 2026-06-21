"use client";

import { useEffect, useState } from "react";
import { Database, Search, Brain, Zap } from "lucide-react";
import { api, type RedisStats } from "@/lib/api";
import { Card, Badge } from "@/components/ui/primitives";

/**
 * Surfaces how Redis is used BEYOND caching — the part of the system that
 * normally runs invisibly in the backend: vector search for intent routing,
 * agent memory, and a semantic LLM cache. Polls every few seconds so a judge
 * sees the numbers move during a live demo.
 */
export function RedisPanel() {
  const [s, setS] = useState<RedisStats | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .getRedisStats()
        .then((d) => alive && setS(d))
        .catch(() => {});
    tick();
    const t = setInterval(tick, 4000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (!s) return null;

  const vr = s.vector_routing;
  const am = s.agent_memory;
  const sc = s.semantic_cache;

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database size={15} className="text-[#dc382d]" />
          <span className="text-sm font-semibold text-ink">Redis · beyond caching</span>
          {s.version && (
            <span className="font-mono text-[10px] text-muted">v{s.version}</span>
          )}
        </div>
        <Badge tone={s.connection === "cloud" ? "accent" : "neutral"}>
          {s.connection === "cloud" ? "Redis Cloud" : "local"}
        </Badge>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {/* Vector search */}
        <Pillar
          icon={<Search size={14} />}
          title="Vector search"
          subtitle="semantic intent routing"
          available={vr.available}
        >
          <Row k="Routines indexed" v={String(vr.indexed_routines ?? 0)} />
          <Row k="Dimensions" v={String(vr.dim ?? 0)} />
          <Row
            k="Match threshold"
            v={vr.threshold !== undefined ? vr.threshold.toFixed(2) : "—"}
          />
          {s.last_match?.similarity != null && (
            <Row
              k="Last match"
              v={`${Math.round((s.last_match.similarity ?? 0) * 100)}%`}
              accent
            />
          )}
        </Pillar>

        {/* Agent memory */}
        <Pillar
          icon={<Brain size={14} />}
          title="Agent memory"
          subtitle="runs + learned values"
          available={am.available}
        >
          <Row k="Runs stored" v={String(am.runs_stored ?? 0)} />
          <Row k="Learned variables" v={String(am.learned_variables ?? 0)} />
        </Pillar>

        {/* Semantic cache */}
        <Pillar
          icon={<Zap size={14} />}
          title="Semantic cache"
          subtitle="LLM calls skipped by meaning"
          available={sc.available}
        >
          <Row k="Cached" v={String(sc.entries ?? 0)} />
          <Row k="Hits / misses" v={`${sc.hits ?? 0} / ${sc.misses ?? 0}`} />
          <Row
            k="Hit rate"
            v={sc.hit_rate !== undefined ? `${Math.round(sc.hit_rate * 100)}%` : "—"}
            accent={(sc.hit_rate ?? 0) > 0}
          />
        </Pillar>
      </div>
    </Card>
  );
}

function Pillar({
  icon,
  title,
  subtitle,
  available,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  available: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-edge bg-panel2/40 p-3">
      <div className="flex items-center gap-2">
        <span className="text-accent">{icon}</span>
        <span className="text-[13px] font-medium text-ink">{title}</span>
        <span
          className={`ml-auto h-1.5 w-1.5 rounded-full ${available ? "bg-ok" : "bg-muted"}`}
          title={available ? "active" : "inactive"}
        />
      </div>
      <div className="mt-0.5 text-[10px] text-muted">{subtitle}</div>
      <div className="mt-2.5 space-y-1">{children}</div>
    </div>
  );
}

function Row({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[11px] text-muted">{k}</span>
      <span
        className={`font-mono text-[12px] tabular-nums ${accent ? "text-accent" : "text-ink"}`}
      >
        {v}
      </span>
    </div>
  );
}
