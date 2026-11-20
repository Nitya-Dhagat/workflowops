import { Suspense } from "react";
import { RequestDetail } from "./request-detail";

// useParams suspends for request-time params under cacheComponents.
export default function Page() {
  return (
    <Suspense fallback={<p className="py-6 text-center text-sm text-zinc-500">Loading…</p>}>
      <RequestDetail />
    </Suspense>
  );
}
