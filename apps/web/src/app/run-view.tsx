"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type PlatformEvent, type RunDetail } from "@/lib/api";
import { ApprovalCard } from "./approval-card";
import { Card, RiskBadge, StateMessage, StatusBadge } from "./ui";

const ACTIVE = new Set(["running", "pending"]);

export function RunView({ runId }: { runId: string }) {
  const detail = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api<RunDetail>(`/workflows/runs/${runId}`),
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.run.status) ? 1500 : false),
  });
  const events = useQuery({
    queryKey: ["run-events", runId, detail.data?.run.status, detail.data?.request_status],
    queryFn: () => api<PlatformEvent[]>(`/workflows/runs/${runId}/events`),
  });

  if (detail.isLoading) return <StateMessage kind="loading">Loading run…</StateMessage>;
  if (detail.error) return <StateMessage kind="error">{detail.error.message}</StateMessage>;
  const d = detail.data!;
  const transitions = (events.data ?? []).filter((e) => e.event_type === "state_transition");
  const report = d.run.state.report;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <StatusBadge status={d.request_status} />
        <span className="text-zinc-500">
          framework <b>{d.run.framework}</b> · mode {d.run.mode} · run {d.run.status}
        </span>
      </div>
      {d.request_status_reason && <p className="text-sm text-zinc-600 dark:text-zinc-300">{d.request_status_reason}</p>}

      {report && (
        <Card title="Report (from execution records)">
          <p className="text-sm">{report.summary}</p>
          {!!report.next_steps?.length && (
            <ul className="mt-2 list-disc pl-5 text-sm">
              {report.next_steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <Card title="Stage timeline">
        <ol className="flex flex-wrap gap-2 text-xs">
          {transitions.map((t) => (
            <li key={t.id} className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800" title={new Date(t.created_at).toLocaleString()}>
              {String(t.payload.to).replaceAll("_", " ")}
            </li>
          ))}
        </ol>
      </Card>

      {d.approvals.some((a) => a.status === "pending") && (
        <Card title="Awaiting your approval">
          <div className="space-y-3">
            {d.approvals.filter((a) => a.status === "pending").map((a) => (
              <ApprovalCard key={a.id} approval={a} />
            ))}
          </div>
        </Card>
      )}

      <Card title="Proposed actions">
        {d.actions.length === 0 ? (
          <StateMessage kind="empty">No actions were proposed.</StateMessage>
        ) : (
          <ul className="space-y-3">
            {d.actions.map((a) => (
              <li key={a.id} className="rounded border border-zinc-200 p-3 dark:border-zinc-800">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-xs text-zinc-500">{a.key}</code>
                  <RiskBadge risk={a.risk_class} />
                  <span className="font-medium">{a.action_type.replaceAll("_", " ")}</span>
                  <StatusBadge status={a.status} />
                </div>
                {a.validation_notes.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-xs text-zinc-600 dark:text-zinc-400">
                    {a.validation_notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                )}
                {a.result && (
                  <p className="mt-1 text-xs text-zinc-500">result: {JSON.stringify(a.result)}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Agent executions">
          <table className="w-full text-left text-xs">
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {d.agent_executions.map((e) => (
                <tr key={e.id}>
                  <td className="py-1 pr-2">{e.stage}</td>
                  <td className="py-1 pr-2 font-medium">{e.agent}</td>
                  <td className="py-1 pr-2"><StatusBadge status={e.status} /></td>
                  <td className="py-1 text-right text-zinc-500">{e.latency_ms.toFixed(0)} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title={`Evidence (${d.evidence.length})`}>
          <ul className="max-h-64 space-y-1 overflow-y-auto text-xs">
            {d.evidence.map((e) => (
              <li key={e.ref}>
                <code>{e.ref}</code> <span className="text-zinc-500">· {e.summary}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title={`Tool calls (${d.tool_invocations.length})`}>
        <ul className="max-h-64 space-y-1 overflow-y-auto text-xs">
          {d.tool_invocations.map((t, i) => (
            <li key={i} className="flex justify-between gap-2">
              <span>
                <b>{t.agent}</b> → <code>{t.tool}</code>
              </span>
              <span className={t.status === "ok" ? "text-zinc-500" : "text-rose-600"}>
                {t.status} · {t.latency_ms.toFixed(0)} ms
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
