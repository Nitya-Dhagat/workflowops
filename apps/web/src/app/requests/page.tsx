"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type RequestItem } from "@/lib/api";
import { Card, StateMessage, StatusBadge, primaryButton } from "../ui";

const REQUESTERS = ["u_01", "u_02", "u_03"];
const FRAMEWORK_LABELS: Record<string, string> = {
  rules: "rules (no AI, instant)",
  gemini: "gemini baseline (AI, ~1 min)",
  adk: "Google ADK (AI, ~1 min)",
  langgraph: "LangGraph (AI, ~1 min)",
};

export default function RequestsPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [description, setDescription] = useState("");
  const [requester, setRequester] = useState("u_01");
  const [framework, setFramework] = useState("rules");
  const frameworks = useQuery({ queryKey: ["frameworks"], queryFn: () => api<string[]>("/frameworks") });

  const list = useQuery({
    queryKey: ["requests", q],
    queryFn: () =>
      api<{ total: number; items: RequestItem[] }>(
        `/requests?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`,
      ),
  });

  const submit = useMutation({
    mutationFn: async () => {
      const req = await api<RequestItem>("/requests", {
        method: "POST",
        body: JSON.stringify({ description, requester_id: requester, department: "partnerships" }),
      });
      await api(`/workflows/${req.id}/run`, { method: "POST", body: JSON.stringify({ framework }) });
      return req;
    },
    onSuccess: () => {
      setDescription("");
      qc.invalidateQueries({ queryKey: ["requests"] });
    },
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Requests</h1>
      <Card title="Submit a request">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (description.trim()) submit.mutate();
          }}
        >
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Description</span>
            <textarea
              required
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g. Please onboard Northwind Cloud as a cloud partner."
              className="w-full rounded-md border border-zinc-300 bg-transparent p-2 dark:border-zinc-700"
            />
          </label>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm">
              <span className="mb-1 block font-medium">Requester</span>
              <select
                value={requester}
                onChange={(e) => setRequester(e.target.value)}
                className="rounded-md border border-zinc-300 bg-transparent p-1.5 dark:border-zinc-700"
              >
                {REQUESTERS.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1 block font-medium">Agent framework</span>
              <select
                value={framework}
                onChange={(e) => setFramework(e.target.value)}
                className="rounded-md border border-zinc-300 bg-transparent p-1.5 dark:border-zinc-700"
              >
                {(frameworks.data ?? ["rules"]).map((f) => (
                  <option key={f} value={f}>
                    {FRAMEWORK_LABELS[f] ?? f}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={submit.isPending} className={primaryButton}>
              {submit.isPending ? "Submitting…" : "Submit & run workflow"}
            </button>
          </div>
          {submit.error && <StateMessage kind="error">{submit.error.message}</StateMessage>}
          {submit.data && (
            <p className="text-sm" role="status">
              Submitted.{" "}
              <Link className="underline" href={`/requests/${submit.data.id}`}>
                View request
              </Link>
            </p>
          )}
        </form>
      </Card>

      <Card
        title={`All requests${list.data ? ` (${list.data.total})` : ""}`}
        actions={
          <input
            aria-label="Search requests"
            placeholder="Search…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="rounded-md border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700"
          />
        }
      >
        {list.isLoading && <StateMessage kind="loading">Loading…</StateMessage>}
        {list.error && <StateMessage kind="error">{list.error.message}</StateMessage>}
        {list.data?.items.length === 0 && <StateMessage kind="empty">No requests match.</StateMessage>}
        {!!list.data?.items.length && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-zinc-500">
                <tr>
                  <th className="py-2 pr-3">Description</th>
                  <th className="py-2 pr-3">Requester</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {list.data.items.map((r) => (
                  <tr key={r.id}>
                    <td className="max-w-md truncate py-2 pr-3">
                      <Link href={`/requests/${r.id}`} className="hover:underline">
                        {r.description}
                      </Link>
                    </td>
                    <td className="py-2 pr-3">{r.requester_id}</td>
                    <td className="py-2 pr-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="py-2 text-zinc-500">{new Date(r.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
