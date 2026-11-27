"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { clearSession, getSession, type Session } from "@/lib/auth";

// Reads the session after mount (localStorage is not available during server rendering).
export function useSession(): { session: Session | null; ready: boolean } {
  const [state, setState] = useState<{ session: Session | null; ready: boolean }>({
    session: null,
    ready: false,
  });
  useEffect(() => {
    const sync = () => setState({ session: getSession(), ready: true });
    sync();
    window.addEventListener("workflowops-session", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("workflowops-session", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return state;
}

// Shows the page only to signed-in users; everyone else is sent to the sign-in page.
export function AuthGate({ children }: { children: ReactNode }) {
  const { session, ready } = useSession();
  const router = useRouter();
  const onLogin = usePathname() === "/login"; // reactive: updates on client-side navigation
  useEffect(() => {
    if (ready && !session && !onLogin) router.replace("/login");
  }, [ready, session, onLogin, router]);
  if (!ready) return <p className="py-6 text-center text-sm text-zinc-500">Loading…</p>;
  if (!session && !onLogin) return null;
  return <>{children}</>;
}

export function UserMenu() {
  const { session } = useSession();
  const router = useRouter();
  if (!session) {
    return (
      <Link href="/login" className="ml-auto text-sm text-sky-600 hover:underline">
        Sign in
      </Link>
    );
  }
  return (
    <div className="ml-auto flex items-center gap-3 text-sm">
      <span className="text-zinc-600 dark:text-zinc-300">
        {session.user.name} <span className="text-zinc-400">· {session.user.role}</span>
      </span>
      <button
        type="button"
        onClick={() => {
          clearSession();
          router.replace("/login");
        }}
        className="rounded-md border border-zinc-300 px-2 py-1 hover:bg-zinc-100 focus-visible:ring-2 focus-visible:ring-sky-500 dark:border-zinc-700 dark:hover:bg-zinc-800"
      >
        Sign out
      </button>
    </div>
  );
}
