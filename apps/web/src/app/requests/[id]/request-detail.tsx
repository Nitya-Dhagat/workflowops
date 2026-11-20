"use client";

import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type RequestItem, type Run } from "@/lib/api";
import { RunView } from "../../run-view";
import { Card, StateMessage, StatusBadge, primaryButton } from "../../ui";

const TERMINAL = new Set(["completed", "completed_with_warnings", "failed", "cancelled"]);

export function RequestDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const req = useQuery({
    queryKey: ["request", id],
    queryFn: () => api<RequestItem>(`/requests/${id}`),
    // Keep polling until the request reaches a terminal status.
    refetchInterval: (q) => (q.state.data && TERMINAL.has(q.state.data.status) ? false : 2000),
  });
  const runs = useQuery({
    queryKey: ["runs", id],
    queryFn: () => api<Run[]>(`/workflows/runs?request_id=${id}`),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "running" || r.status === "pending") ? 1500 : false),
  });
  const start = useMutation({
    mutationFn: () => api(`/workflows/${id}/run`, { method: "POST", body: JSON.stringify({ framework: "rules" }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs", id] }),
  });

  if (req.isLoading) return <StateMessage kind="loading">Loading…</StateMessage>;
  if (req.error) return <StateMessage kind="error">{req.error.message}</StateMessage>;
  const r = req.data!;
  const latest = runs.data?.[0];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Request</h1>
        <p className="mt-1 text-xs text-zinc-500">{r.id}</p>
      </div>
      <Card
        title="Details"
        actions={
          <button className={primaryButton} disabled={start.isPending} onClick={() => start.mutate()}>
            {latest ? "Run again" : "Run workflow"}
          </button>
        }
      >
        <p className="whitespace-pre-wrap text-sm">{r.description}</p>
        <div className="mt-3 flex flex-wrap gap-3 text-xs text-zinc-500">
          <StatusBadge status={r.status} />
          <span>requester {r.requester_id}</span>
          <span>priority {r.priority}</span>
          <span>{new Date(r.created_at).toLocaleString()}</span>
        </div>
        {start.error && <StateMessage kind="error">{start.error.message}</StateMessage>}
      </Card>
      {latest ? (
        <RunView runId={latest.id} />
      ) : (
        <StateMessage kind="empty">No workflow runs yet.</StateMessage>
      )}
    </div>
  );
}
