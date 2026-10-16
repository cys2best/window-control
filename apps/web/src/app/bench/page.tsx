import { Suspense } from "react";
import BenchPageClient from "./BenchPageClient";

// Lab-only latency bench (scripts/bench). The host serves this page only
// when started with EMUCTRL_BENCH=1.
export default function BenchPage() {
  return (
    <Suspense>
      <BenchPageClient />
    </Suspense>
  );
}
