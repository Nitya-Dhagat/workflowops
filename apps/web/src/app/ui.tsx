// Small shared presentational components.
import type { ReactNode } from "react";

const STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  verified: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  completed_with_warnings: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  awaiting_approval: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  pending_approval: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  pending: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  awaiting_requester_info: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
  failed: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  rejected: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  rejected_by_validation: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
};

export function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300";
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}

export function RiskBadge({ risk }: { risk: string }) {
  const cls =
    risk === "high"
      ? "border-rose-500 text-rose-700 dark:text-rose-300"
      : risk === "medium"
        ? "border-amber-500 text-amber-700 dark:text-amber-300"
        : "border-zinc-400 text-zinc-600 dark:text-zinc-300";
  return <span className={`rounded border px-1.5 py-0.5 text-xs font-semibold uppercase ${cls}`}>{risk}</span>;
}

export function Card({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function StateMessage({ kind, children }: { kind: "loading" | "error" | "empty"; children: ReactNode }) {
  const cls =
    kind === "error" ? "text-rose-600 dark:text-rose-400" : "text-zinc-500 dark:text-zinc-400";
  return (
    <p role={kind === "error" ? "alert" : "status"} className={`py-6 text-center text-sm ${cls}`}>
      {children}
    </p>
  );
}

export const button =
  "rounded-md px-3 py-1.5 text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:opacity-50";
export const primaryButton = `${button} bg-sky-600 text-white hover:bg-sky-700`;
export const secondaryButton = `${button} border border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800`;
