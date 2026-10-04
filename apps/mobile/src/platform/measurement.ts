import { Share } from "react-native";
import type { RemoteRunEvidence } from "@wc/core";

export async function exportMeasurement(run: RemoteRunEvidence): Promise<void> {
  await Share.share({ title: "Remote measurement JSON", message: JSON.stringify(run, null, 2) });
}
