"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type Approval } from "@/lib/api";
import { ApprovalCard } from "../approval-card";
import { Card, StateMessage } from "../ui";

export default function ApprovalsPage() {
  const q = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api<Approval[]>("/approvals"),
    refetchInterval: 5000,
  });
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Approvals</h1>
      <Card title="Pending">
        {q.isLoading && <StateMessage kind="loading">Loading…</StateMessage>}
        {q.error && <StateMessage kind="error">{q.error.message}</StateMessage>}
        {q.data?.length === 0 && <StateMessage kind="empty">Nothing waiting for review.</StateMessage>}
        <div className="space-y-3">
          {q.data?.map((a) => (
            <div key={a.id}>
              <ApprovalCard approval={a} />
              <Link href={`/runs/${a.run_id}`} className="mt-1 inline-block text-xs text-zinc-500 hover:underline">
                Open run
              </Link>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
