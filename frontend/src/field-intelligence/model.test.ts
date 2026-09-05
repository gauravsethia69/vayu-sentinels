import { describe, expect, it } from "vitest";
import type { FieldReport } from "../api/types";
import { buildFieldReportPayload, filterFieldReports, initialFieldReportForm, verificationLabel } from "./model";

const report = (status: FieldReport["status"], verification_state = "pending_sensor_confirmation") => ({
  id: status, created_at: "2026-08-31T10:00:00Z", updated_at: "2026-08-31T10:00:00Z",
  reporter_type: "controller", category: "clouds_approaching", observation: "Clouds nearby",
  severity: "moderate", reporter_confidence: "high", radius_km: 10, status,
  source: "test", verification_state, corroboration_confidence: 0, verified_by_nodes: [],
  evidence: [], contradicting_evidence: [], message: "Pending", nearby_stations: [],
}) as FieldReport;

describe("field intelligence presentation", () => {
  it("builds cluster payloads without inventing coordinates", () => {
    const payload = buildFieldReportPayload({ ...initialFieldReportForm, observation: "Clouds approaching" });
    expect(payload.cluster_id).toBe("prototype_cluster_01");
    expect(payload.station_id).toBeUndefined();
    expect(payload.expires_in_minutes).toBe(60);
  });

  it("builds station issues that remain active until resolved", () => {
    const payload = buildFieldReportPayload({
      ...initialFieldReportForm,
      reportType: "station",
      category: "sensor_damage",
      target: "AWS_001",
      observation: "Housing cracked",
      expiry: "until",
    });
    expect(payload.station_id).toBe("AWS_001");
    expect(payload.until_resolved).toBe(true);
  });

  it("filters history without promoting reports to verified", () => {
    const reports = [report("active"), report("resolved"), report("expired")];
    expect(filterFieldReports(reports, "active")).toHaveLength(1);
    expect(filterFieldReports(reports, "all")).toHaveLength(3);
    expect(verificationLabel(report("active", "partially_supported"))).toBe("Partially Supported");
    expect(verificationLabel(report("active"))).toBe("Pending Sensor Confirmation");
  });
});
