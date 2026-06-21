"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Shield, Zap, Box } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Button, Card, CardBody, CardHeader } from "@/components/ui/primitives";

interface ScreenRule {
  name: string;
  match_text: string[];
  action: string;
  reason: string;
}

interface PolicyDoc {
  version?: number;
  screen_rules?: ScreenRule[];
  triggers?: Record<string, string>;
  containment?: {
    allowed_apps?: string[];
    allowed_domains?: string[];
    max_actions_per_minute?: number;
    max_steps_per_run?: number;
  };
}

const VERDICT_TONE: Record<string, "halt" | "flag" | "ok" | "neutral"> = {
  halt: "halt",
  flag: "flag",
  ok: "ok",
};

export default function PolicyPage() {
  const [policy, setPolicy] = useState<PolicyDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    try {
      const p = (await api.getPolicy()) as PolicyDoc;
      setPolicy(p);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <div>
      <PageHeader
        title="Governance Policy"
        subtitle="Live policy.yaml — edit the file and Shepherd reloads it without a restart."
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void load()}
            disabled={refreshing}
          >
            <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
            Reload
          </Button>
        }
      />

      <div className="space-y-4 p-6">
        {loading ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-24 animate-pulse rounded-xl border border-edge bg-panel/50"
              />
            ))}
          </div>
        ) : !policy ? (
          <div className="rounded-xl border border-edge bg-panel/40 px-6 py-10 text-center text-sm text-muted">
            Could not load policy.yaml. Is the backend running?
          </div>
        ) : (
          <>
            {/* Screen rules */}
            {policy.screen_rules && policy.screen_rules.length > 0 && (
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                    <Shield size={15} className="text-accent" />
                    Screen rules ({policy.screen_rules.length})
                  </div>
                  <p className="mt-0.5 text-xs text-muted">
                    OCR text matched at each step boundary
                  </p>
                </CardHeader>
                <CardBody className="space-y-2">
                  {policy.screen_rules.map((r) => (
                    <div
                      key={r.name}
                      className="flex items-start justify-between gap-4 rounded-lg border border-edge bg-panel2/60 px-3 py-2.5"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-sm text-ink">
                          {r.name}
                        </div>
                        <div className="mt-0.5 text-xs text-muted">
                          {r.reason}
                        </div>
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {r.match_text.map((kw) => (
                            <span
                              key={kw}
                              className="rounded border border-edge bg-panel px-1.5 py-0.5 font-mono text-[10px] text-muted"
                            >
                              {kw}
                            </span>
                          ))}
                        </div>
                      </div>
                      <Badge tone={VERDICT_TONE[r.action] ?? "neutral"}>
                        {r.action}
                      </Badge>
                    </div>
                  ))}
                </CardBody>
              </Card>
            )}

            {/* Triggers */}
            {policy.triggers && Object.keys(policy.triggers).length > 0 && (
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                    <Zap size={15} className="text-flag" />
                    Trigger overrides
                  </div>
                  <p className="mt-0.5 text-xs text-muted">
                    Planted monitor_trigger values override the default verdicts
                  </p>
                </CardHeader>
                <CardBody>
                  <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                    {Object.entries(policy.triggers).map(([name, action]) => (
                      <div
                        key={name}
                        className="flex items-center justify-between rounded-lg border border-edge bg-panel2/60 px-3 py-2"
                      >
                        <span className="font-mono text-xs text-ink">
                          {name}
                        </span>
                        <Badge tone={VERDICT_TONE[action] ?? "neutral"}>
                          {action}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </CardBody>
              </Card>
            )}

            {/* Containment */}
            {policy.containment && (
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                    <Box size={15} className="text-muted" />
                    Containment sandbox
                  </div>
                  <p className="mt-0.5 text-xs text-muted">
                    Restricts which apps, domains, and rate limits the agent can use
                  </p>
                </CardHeader>
                <CardBody className="space-y-4">
                  <div className="grid grid-cols-2 gap-3">
                    <Metric
                      label="Max actions / min"
                      value={
                        policy.containment.max_actions_per_minute?.toString() ??
                        "∞"
                      }
                    />
                    <Metric
                      label="Max steps / run"
                      value={
                        policy.containment.max_steps_per_run?.toString() ?? "∞"
                      }
                    />
                  </div>
                  {policy.containment.allowed_apps && (
                    <div>
                      <div className="mb-1.5 text-xs font-medium text-muted uppercase tracking-wide">
                        Allowed apps
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {policy.containment.allowed_apps.map((a) => (
                          <span
                            key={a}
                            className="rounded border border-ok/30 bg-ok/5 px-2 py-0.5 text-xs text-ok"
                          >
                            {a}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {policy.containment.allowed_domains && (
                    <div>
                      <div className="mb-1.5 text-xs font-medium text-muted uppercase tracking-wide">
                        Allowed domains
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {policy.containment.allowed_domains.map((d) => (
                          <span
                            key={d}
                            className="rounded border border-accent/30 bg-accent/5 px-2 py-0.5 font-mono text-xs text-accent"
                          >
                            {d}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </CardBody>
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-edge bg-panel2/60 p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-lg font-semibold text-ink">{value}</div>
    </div>
  );
}
