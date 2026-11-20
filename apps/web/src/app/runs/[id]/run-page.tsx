"use client";

import { useParams } from "next/navigation";
import { RunView } from "../../run-view";

export function RunPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Workflow run</h1>
      <RunView runId={id} />
    </div>
  );
}
