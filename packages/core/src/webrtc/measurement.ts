import type { StreamTelemetry } from "./telemetry";

export type RemoteSampleEvidence = {
  elapsed_s: number;
  source_width: number | null;
  source_height: number | null;
  decoded_width: number | null;
  decoded_height: number | null;
  decoded_fps: number | null;
  target_bitrate_mbps: number | null;
  bitrate_mbps: number | null;
  adaptive_downgrades: number;
  total_freeze_s: number | null;
  max_freeze_s: number | null;
  route: StreamTelemetry["route"];
  address_family: StreamTelemetry["addressFamily"];
  relay_protocol: StreamTelemetry["relayProtocol"];
};
export type RemoteRunEvidence = {
  capacity_mbps: number | null;
  target_bitrate_mbps: number | null;
  expected_route: string;
  samples: RemoteSampleEvidence[];
};
const measured = (n: number | null): number | null => typeof n === "number" && Number.isFinite(n) && n >= 0 ? n : null;

export function makeMeasurementRecorder(meta: { targetBitrateMbps: number; capacityMbps: number | null; expectedRoute: string }, now: () => number = () => performance.now()) {
  const samples: RemoteSampleEvidence[] = [];
  let start: number | null = null, previousTime: number | null = null;
  let route: string | undefined;
  let stopped = false;
  const target = measured(meta.targetBitrateMbps), capacity = measured(meta.capacityMbps);
  const expected = ["direct", "turn_udp", "turn_tcp", "turn_tls", "unknown"].includes(meta.expectedRoute) ? meta.expectedRoute : "unknown";
  return {
    add(sample: StreamTelemetry, adaptiveDowngrades: number): void {
      if (stopped) return;
      const time = now();
      if (!Number.isFinite(time) || !Number.isFinite(adaptiveDowngrades) || adaptiveDowngrades < 0 || previousTime !== null && time < previousTime) return;
      // Begin only once interval baselines have produced actual measurements.
      if (start === null && (measured(sample.bitrateMbps) === null || measured(sample.decodedFps) === null)) return;
      const identity = `${sample.route}/${sample.addressFamily}/${sample.relayProtocol}`;
      if (route !== undefined && route !== identity) { stopped = true; return; }
      start ??= time; route = identity; previousTime = time;
      samples.push({
        elapsed_s: (time - start) / 1_000,
        source_width: measured(sample.sourceWidth), source_height: measured(sample.sourceHeight),
        decoded_width: measured(sample.decodedWidth), decoded_height: measured(sample.decodedHeight), decoded_fps: measured(sample.decodedFps),
        target_bitrate_mbps: target, bitrate_mbps: measured(sample.bitrateMbps), adaptive_downgrades: adaptiveDowngrades,
        total_freeze_s: measured(sample.totalFreezeSeconds), max_freeze_s: measured(sample.maxFreezeSeconds),
        route: ["direct", "relay", "unknown"].includes(sample.route) ? sample.route : "unknown",
        address_family: ["IPv4", "IPv6", "mixed", "unknown"].includes(sample.addressFamily) ? sample.addressFamily : "unknown",
        relay_protocol: ["udp", "tcp", "tls", "unknown"].includes(sample.relayProtocol) ? sample.relayProtocol : "unknown",
      });
      // Missing/reset interval evidence ends this epoch instead of splicing a new pair.
      if (sample.bitrateMbps === null || sample.decodedFps === null) stopped = true;
    },
    exportRun(): RemoteRunEvidence {
      return { capacity_mbps: capacity, target_bitrate_mbps: target, expected_route: expected, samples: samples.map(s => ({ ...s })) };
    },
  };
}
