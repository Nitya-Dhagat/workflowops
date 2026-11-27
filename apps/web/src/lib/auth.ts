// Signed-in session kept in this browser only (localStorage). The API re-checks every request.
export type SessionUser = { id: string; name: string; role: "requester" | "reviewer"; department: string };
export type Session = { token: string; user: SessionUser };

const KEY = "workflowops.session";

export function getSession(): Session | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function setSession(s: Session): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage unavailable: the user will be asked to sign in again */
  }
  window.dispatchEvent(new Event("workflowops-session"));
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event("workflowops-session"));
}
