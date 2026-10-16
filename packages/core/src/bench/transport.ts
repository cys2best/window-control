export type SelectedPair = { id: string | null; remoteType: string | null };

export function selectedCandidatePair(stats: { forEach(cb: (report: any) => void): void }): SelectedPair {
  const reports: any[] = [];
  stats.forEach((report) => reports.push(report));
  const byId = new Map(reports.map((r) => [r.id, r]));
  const selectedId = reports.find((r) => r.type === "transport" && r.selectedCandidatePairId)?.selectedCandidatePairId
    ?? reports.find((r) => r.type === "candidate-pair" && r.state === "succeeded" && r.nominated)?.id
    ?? null;
  const pair = selectedId ? byId.get(selectedId) : undefined;
  if (!pair) return { id: null, remoteType: null };
  return { id: pair.id, remoteType: byId.get(pair.remoteCandidateId)?.candidateType ?? null };
}
