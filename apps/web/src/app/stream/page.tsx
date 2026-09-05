import { Suspense } from "react";
import StreamPageClient from "./StreamPageClient";

// useSearchParams() (in StreamPageClient) requires a Suspense boundary so
// Next.js can prerender a static shell for `output: "export"` without
// blocking on values that are only known client-side.
export default function StreamPage() {
  return (
    <Suspense>
      <StreamPageClient />
    </Suspense>
  );
}
