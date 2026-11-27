import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import "./globals.css";
import { Providers } from "./providers";
import { AuthGate, UserMenu } from "./session";

export const metadata: Metadata = {
  title: "WorkflowOps",
  description: "Human-supervised AI operations platform",
};

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/requests", label: "Requests" },
  { href: "/approvals", label: "Approvals" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
        <Providers>
          <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
            <nav aria-label="Main" className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
              <span className="font-semibold">WorkflowOps</span>
              {NAV.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className="text-sm text-zinc-600 hover:text-zinc-900 focus-visible:underline dark:text-zinc-300 dark:hover:text-white"
                >
                  {n.label}
                </Link>
              ))}
              <UserMenu />
            </nav>
          </header>
          <main className="mx-auto max-w-6xl px-4 py-6">
            {/* usePathname suspends on dynamic routes under cacheComponents */}
            <Suspense fallback={<p className="py-6 text-center text-sm text-zinc-500">Loading…</p>}>
              <AuthGate>{children}</AuthGate>
            </Suspense>
          </main>
        </Providers>
      </body>
    </html>
  );
}
