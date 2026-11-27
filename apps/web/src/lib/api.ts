// Thin typed client for the WorkflowOps API (/api/v1).
import { clearSession, getSession } from "./auth";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type RequestItem = {
  id: string;
  description: string;
  requester_id: string;
  department: string;
  priority: string;
  status: string;
  status_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type Run = {
  id: string;
  request_id: string;
  framework: string;
  mode: string;
  status: string;
  current_stage: string | null;
  state: Record<string, unknown> & { report?: { summary: string; next_steps?: string[] } };
  error: string | null;
  created_at: string;
};

export type Action = {
  id: string;
  key: string;
  action_type: string;
  params: Record<string, unknown>;
  evidence_refs: string[];
  risk_class: "low" | "medium" | "high";
  status: string;
  validation_notes: string[];
  result: Record<string, unknown> | null;
};

export type Approval = {
  id: string;
  action_id: string;
  run_id: string;
  status: string;
  action_hash: string;
  risk_class: string;
  expires_at: string;
  decided_by: string | null;
  decided_at: string | null;
  comment: string | null;
  action: { action_type: string; params: Record<string, unknown>; evidence_refs: string[]; notes: string[] };
  request_description?: string | null;
};

export type RunDetail = {
  run: Run;
  request_status: string;
  request_status_reason: string | null;
  actions: Action[];
  approvals: Approval[];
  agent_executions: { id: string; stage: string; agent: string; status: string; latency_ms: number; error: string | null; input_tokens: number; output_tokens: number; cost_usd_estimate: number }[];
  evidence: { ref: string; source: string; summary: string; collected_by: string }[];
  tool_invocations: { agent: string; tool: string; status: string; latency_ms: number }[];
};

export type PlatformEvent = {
  id: string;
  seq: number;
  event_type: string;
  stage: string | null;
  agent: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const session = typeof window !== "undefined" ? getSession() : null;
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (res.status === 401 && !path.startsWith("/auth/")) {
    clearSession(); // expired or invalid: AuthGate sends the user back to sign in
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = body?.error?.message ?? msg;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json() as Promise<T>;
}
