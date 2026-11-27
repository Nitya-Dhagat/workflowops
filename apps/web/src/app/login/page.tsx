"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { setSession, type SessionUser } from "@/lib/auth";
import { Card, StateMessage, primaryButton } from "../ui";

type DemoUser = { id: string; name: string; role: string };

export default function LoginPage() {
  const router = useRouter();
  const [userId, setUserId] = useState("u_01");
  const [password, setPassword] = useState("");
  const users = useQuery({ queryKey: ["demo-users"], queryFn: () => api<DemoUser[]>("/auth/demo-users") });
  const login = useMutation({
    mutationFn: () =>
      api<{ access_token: string; user: SessionUser }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ user_id: userId, password }),
      }),
    onSuccess: (r) => {
      setSession({ token: r.access_token, user: r.user });
      router.replace("/requests");
    },
  });

  return (
    <div className="mx-auto max-w-md space-y-4">
      <h1 className="text-2xl font-semibold">Sign in</h1>
      <Card title="Account">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            login.mutate();
          }}
        >
          <label className="block text-sm" htmlFor="login-user">
            <span className="mb-1 block font-medium">Username</span>
            {/* Any account works; the list only suggests the synthetic demo accounts. */}
            <input
              id="login-user"
              list="demo-accounts"
              required
              autoComplete="username"
              value={userId}
              onChange={(e) => setUserId(e.target.value.trim())}
              className="w-full rounded-md border border-zinc-300 bg-transparent p-2 dark:border-zinc-700"
            />
            <datalist id="demo-accounts">
              {(users.data ?? []).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.role})
                </option>
              ))}
            </datalist>
          </label>
          <label className="block text-sm" htmlFor="login-password">
            <span className="mb-1 block font-medium">Password</span>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-zinc-300 bg-transparent p-2 dark:border-zinc-700"
            />
          </label>
          <button type="submit" disabled={login.isPending} className={primaryButton}>
            {login.isPending ? "Signing in…" : "Sign in"}
          </button>
          {login.error && <StateMessage kind="error">{login.error.message}</StateMessage>}
        </form>
      </Card>
      <p className="text-sm text-zinc-500">
        Demo accounts are synthetic. Requesters submit requests; reviewers (u_10 to u_12) approve or reject
        actions. The demo password is <code className="rounded bg-zinc-200 px-1 dark:bg-zinc-800">workflowops-demo</code>.
      </p>
    </div>
  );
}
