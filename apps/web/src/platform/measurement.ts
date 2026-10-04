import type { RemoteRunEvidence } from "@wc/core";

export function exportMeasurement(run: RemoteRunEvidence): void {
  const blob = new Blob([JSON.stringify(run, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  try {
    link.href = url; link.download = "remote-measurement.json";
    document.body.appendChild(link);
    link.click();
  } finally { link.remove(); URL.revokeObjectURL(url); }
}
