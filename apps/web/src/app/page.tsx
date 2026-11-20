"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type Approval, type RequestItem } from "@/lib/api";
import { Card, StateMessage, StatusBadge } from "./ui";

export default function Overview() {
  const requests = useQuery({
    queryKey: ["requests", "all"],
    queryFn: () => api<{ total: number; items: RequestItem[] }>("/requests?limit=200"),
  });
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api<Approval[]>("/approvals") });

  if (requests.isLoading) return <StateMessage kind="loading">Loading…</StateMessage>;
  if (requests.error)
    return <StateMessage kind="error">Could not reach the API: {requests.error.message}</StateMessage>;

  const items = requests.data?.items ?? [];
  const counts = items.reduce<Record<string, number>>((acc, r) => {
    acc[r.status] = (acc[r.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Overview</h1>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi label="Requests" value={requests.data?.total ?? 0} />
        <Kpi label="Pending approvals" value={approvals.data?.length ?? 0} href="/approvals" />
        <Kpi label="Completed" value={(counts.completed ?? 0) + (counts.completed_with_warnings ?? 0)} />
        <Kpi label="Failed / rejected" value={counts.failed ?? 0} />
      </div>
      <Card title="Recent requests">
        {items.length === 0 ? (
          <StateMessage kind="empty">
            No requests yet.{" "}
            <Link className="underline" href="/requests">
              Submit one
            </Link>
            .
          </StateMessage>
        ) : (
          <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {items.slice(0, 8).map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-3 py-2">
                <Link href={`/requests/${r.id}`} className="truncate text-sm hover:underline">
                  {r.description}
                </Link>
                <StatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function Kpi({ label, value, href }: { label: string; value: number; href?: string }) {
  const body = (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}
