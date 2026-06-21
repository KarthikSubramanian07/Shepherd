"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, ShieldCheck, ShieldAlert, AlertTriangle } from "lucide-react";
import { api, type AuditEntry, type AuditVerification } from "@/lib/api";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Button, Card } from "@/components/ui/primitives";

function iso(ts: number): string {
  return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19);
}

const STATUS_COLORS: Record<string, string> = {
  completed: "text-ok",
  failed: "text-halt",
  halted: "text-halt",
  flagged: "text-flag",
  aborted: "text-muted",
};

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [verify, setVerify] = useState<AuditVerification | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    try {
      const [log, chain] = await Promise.all([
        api.getAuditLog(),
        api.verifyAuditChain(),
      ]);
      setEntries(log);
      setVerify(chain);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <PageHeader
        title="Audit Log"
        subtitle="Tamper-evident SHA-256 hash chain · every action the agent took."
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void load()}
            disabled={refreshing}
          >
            <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
            Refresh
          </Button>
        }
      />

      <div className="space-y-4 p-6">
        {/* Chain integrity banner */}
        {verify && (
          <Card
            className={`flex items-center gap-3 p-4 ${
              verify.valid
                ? "border-ok/30 bg-ok/5"
                : "border-halt/40 bg-halt/5"
            }`}
          >
            {verify.valid ? (
              <ShieldCheck size={20} className="shrink-0 text-ok" />
            ) : (
              <ShieldAlert size={20} className="shrink-0 text-halt" />
            )}
            <div className="flex-1">
              <div
                className={`font-semibold ${verify.valid ? "text-ok" : "text-halt"}`}
              >
                {verify.valid
                  ? `Chain intact · ${verify.entries} entries verified`
                  : `Chain broken at entry ${verify.tampered_at ?? "?"}`}
              </div>
              <div className="mt-0.5 text-xs text-muted">{verify.reason}</div>
            </div>
            {verify.valid && (
              <Badge tone="ok">
                <CheckCircle2 size={11} /> verified
              </Badge>
            )}
            {!verify.valid && (
              <Badge tone="halt">
                <AlertTriangle size={11} /> tampered
              </Badge>
            )}
          </Card>
        )}

        {/* Entry table */}
        {loading ? (
          <div className="space-y-2">
            {[0, 1, 2, 4, 5].map((i) => (
              <div
                key={i}
                className="h-12 animate-pulse rounded-xl border border-edge bg-panel/50"
              />
            ))}
          </div>
        ) : entries.length === 0 ? (
          <div className="rounded-xl border border-edge bg-panel/40 px-6 py-10 text-center text-sm text-muted">
            No audit entries yet. Run a routine to generate the first record.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-edge bg-panel/80">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-edge text-left text-muted">
                  <th className="px-4 py-3 font-medium">#</th>
                  <th className="px-4 py-3 font-medium">Run</th>
                  <th className="px-4 py-3 font-medium">Step</th>
                  <th className="px-4 py-3 font-medium">Action</th>
                  <th className="px-4 py-3 font-medium">Target</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">ms</th>
                  <th className="px-4 py-3 font-medium">Time</th>
                  <th className="px-4 py-3 font-medium">Hash</th>
                </tr>
              </thead>
              <tbody>
                {[...entries].reverse().map((e) => (
                  <tr
                    key={e.seq}
                    className="border-b border-edge/50 hover:bg-panel2/50"
                  >
                    <td className="px-4 py-2.5 font-mono text-muted">
                      {e.seq}
                    </td>
                    <td className="max-w-[80px] truncate px-4 py-2.5 font-mono text-muted">
                      {e.run_id}
                    </td>
                    <td className="px-4 py-2.5 text-center text-muted">
                      {e.step_index}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-ink">
                      {e.action}
                    </td>
                    <td className="max-w-[120px] truncate px-4 py-2.5 text-muted">
                      {e.target ?? "·"}
                    </td>
                    <td
                      className={`px-4 py-2.5 font-medium ${STATUS_COLORS[e.status] ?? "text-ink"}`}
                    >
                      {e.status}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-muted">
                      {e.duration_ms}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{iso(e.ts)}</td>
                    <td className="px-4 py-2.5 font-mono text-[10px] text-muted/60">
                      {e.hash.slice(0, 12)}…
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
