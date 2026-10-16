import { selectedCandidatePair } from "./transport";

test("reads the selected pair and its remote candidate type", () => {
  const stats = new Map<string, any>([
    ["T1", { type: "transport", selectedCandidatePairId: "P2" }],
    ["P1", { type: "candidate-pair", id: "P1", state: "succeeded", remoteCandidateId: "R1" }],
    ["P2", { type: "candidate-pair", id: "P2", state: "succeeded", remoteCandidateId: "R2" }],
    ["R1", { type: "remote-candidate", id: "R1", candidateType: "host" }],
    ["R2", { type: "remote-candidate", id: "R2", candidateType: "relay" }],
  ]);
  expect(selectedCandidatePair(stats)).toEqual({ id: "P2", remoteType: "relay" });
});

test("returns nulls without a selected pair", () => {
  expect(selectedCandidatePair(new Map())).toEqual({ id: null, remoteType: null });
});
