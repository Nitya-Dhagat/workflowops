"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Approval } from "@/lib/api";
import { RiskBadge, StateMessage, StatusBadge, primaryButton, secondaryButton } from "./ui";

const REVIEWERS = ["u_10", "u_11", "u_12"];

export function ApprovalCard({ approval }: { approval: Approval }) {
  const qc = useQueryClient();
  const [reviewer, setReviewer] = useState(REVIEWERS[0]);
  const [comment, setComment] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject" | "request_changes") =>
      api(`/approvals/${approval.id}/decision`, {
        method: "POST",
        // The hash binds the decision to exactly what the reviewer is looking at.
        body: JSON.stringify({ decision, reviewer_id: reviewer, action_hash: approval.action_hash, comment: comment || null }),
      }),
    onSuccess: () => qc.invalidateQueries(),
  });
  const pending = approval.status === "pending";

  return (
    <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <header className="mb-2 flex flex-wrap items-center gap-2">
        <RiskBadge risk={approval.risk_class} />
        <h3 className="font-medium">{approval.action.action_type.replaceAll("_", " ")}</h3>
        <StatusBadge status={approval.status} />
      </header>
      {approval.request_description && (
        <p className="mb-2 text-sm text-zinc-600 dark:text-zinc-300">
          For request: <span className="font-medium">{approval.request_description}</span>
        </p>
      )}
      <pre className="mb-2 overflow-x-auto rounded bg-zinc-100 p-2 text-xs dark:bg-zinc-800">
        {JSON.stringify(approval.action.params, null, 2)}
      </pre>
      <p className="text-xs text-zinc-500">
        Evidence: {approval.action.evidence_refs.join(", ") || "none"} · expires{" "}
        {new Date(approval.expires_at).toLocaleString()}
      </p>
      {approval.decided_by && (
        <p className="mt-1 text-xs text-zinc-500">
          Decided by {approval.decided_by} at {new Date(approval.decided_at ?? "").toLocaleString()}
          {approval.comment ? `: “${approval.comment}”` : ""}
        </p>
      )}
      {pending && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select
            aria-label="Reviewer"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            className="rounded-md border border-zinc-300 bg-transparent p-1.5 text-sm dark:border-zinc-700"
          >
            {REVIEWERS.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <input
            aria-label="Comment"
            placeholder="Comment (optional)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            className="min-w-40 flex-1 rounded-md border border-zinc-300 bg-transparent px-2 py-1.5 text-sm dark:border-zinc-700"
          />
          <button className={primaryButton} disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
            Approve
          </button>
          <button className={secondaryButton} disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
            Reject
          </button>
          <button className={secondaryButton} disabled={decide.isPending} onClick={() => decide.mutate("request_changes")}>
            Request changes
          </button>
        </div>
      )}
      {decide.error && <StateMessage kind="error">{decide.error.message}</StateMessage>}
    </article>
  );
}
