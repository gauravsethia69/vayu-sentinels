import type {
  FieldReport,
  FieldReportCategory,
  FieldReportCreatePayload,
  FieldReportSeverity,
  ReporterConfidence,
} from "../api/types";

export const weatherCategories: Array<{ value: FieldReportCategory; label: string }> = [
  ["clouds_approaching", "Clouds Approaching"], ["light_rain", "Light Rain"],
  ["heavy_rain", "Heavy Rain"], ["thunderstorm", "Thunder / Lightning"],
  ["strong_wind", "Strong Wind"], ["fog", "Fog Developing"],
  ["low_visibility", "Low Visibility"], ["hail", "Hail"],
  ["dust_storm", "Dust / Storm"], ["rapid_weather_change", "Rapid Weather Change"],
  ["flooding", "Local Flooding"], ["other_weather", "Other Weather"],
  ["custom_observation", "Custom Observation"],
].map(([value, label]) => ({ value: value as FieldReportCategory, label }));

export const stationCategories: Array<{ value: FieldReportCategory; label: string }> = [
  ["sensor_damage", "Sensor Damaged"], ["sensor_obstruction", "Sensor Obstruction"],
  ["sensor_contamination", "Sensor Contamination"], ["power_issue", "Power Issue"],
  ["communication_issue", "Communication Issue"], ["suspected_sensor_fault", "Suspected Sensor Fault"],
  ["station_access_issue", "Station Access Issue"], ["other_station_issue", "Other Station Issue"],
].map(([value, label]) => ({ value: value as FieldReportCategory, label }));

export const quickReports: Array<{ category: FieldReportCategory; label: string; observation: string }> = [
  { category: "clouds_approaching", label: "Clouds Approaching", observation: "Dark cloud formation approaching from the west." },
  { category: "light_rain", label: "Rain Starting", observation: "Light rainfall has started near the station." },
  { category: "heavy_rain", label: "Heavy Rain", observation: "Heavy rainfall is visible in the local sector." },
  { category: "strong_wind", label: "Strong Wind", observation: "Strong winds are affecting the station area." },
  { category: "fog", label: "Fog", observation: "Fog is developing and visibility is reducing." },
  { category: "low_visibility", label: "Low Visibility", observation: "Local visibility has reduced significantly." },
  { category: "sensor_damage", label: "Sensor Damaged", observation: "A station sensor appears physically damaged." },
  { category: "communication_issue", label: "Communication Issue", observation: "Communication equipment appears unreliable." },
];

export type ReportTarget = "AWS_001" | "AWS_002" | "AWS_003" | "both" | "custom";

export interface FieldReportFormState {
  reportType: "weather" | "station";
  category: FieldReportCategory;
  reporterName: string;
  target: ReportTarget;
  locationLabel: string;
  latitude: string;
  longitude: string;
  observation: string;
  direction: string;
  severity: FieldReportSeverity;
  reporterConfidence: ReporterConfidence;
  radiusKm: number;
  radiusPreset: "1" | "5" | "10" | "25" | "custom";
  expiry: "30" | "60" | "120" | "until";
}

export const initialFieldReportForm: FieldReportFormState = {
  reportType: "weather",
  category: "clouds_approaching",
  reporterName: "Control Room Operator",
  target: "both",
  locationLabel: "Prototype Station Cluster",
  latitude: "",
  longitude: "",
  observation: "",
  direction: "west",
  severity: "moderate",
  reporterConfidence: "high",
  radiusKm: 10,
  radiusPreset: "10",
  expiry: "60",
};

export function buildFieldReportPayload(form: FieldReportFormState): FieldReportCreatePayload {
  const payload: FieldReportCreatePayload = {
    reporter_type: "controller",
    reporter_name: form.reporterName.trim() || undefined,
    category: form.category,
    observation: form.observation.trim(),
    severity: form.severity,
    reporter_confidence: form.reporterConfidence,
    direction: form.direction || null,
    radius_km: form.radiusKm,
    location_label: form.locationLabel.trim() || undefined,
    source: "controller_ui",
  };
  if (form.target === "both") payload.cluster_id = "prototype_cluster_01";
  if (["AWS_001", "AWS_002", "AWS_003"].includes(form.target)) payload.station_id = form.target as FieldReportCreatePayload["station_id"];
  if (form.target === "custom") {
    payload.latitude = Number(form.latitude);
    payload.longitude = Number(form.longitude);
  }
  if (form.expiry === "until") payload.until_resolved = true;
  else payload.expires_in_minutes = Number(form.expiry);
  return payload;
}

export function filterFieldReports(reports: FieldReport[], status: "active" | "resolved" | "expired" | "all") {
  return status === "all" ? reports : reports.filter((report) => report.status === status);
}

export function verificationLabel(report: FieldReport) {
  if (report.status === "resolved") return "Resolved";
  if (report.status === "expired") return "Expired";
  return report.verification_state === "corroborated"
    ? "Sensor Evidence Supports Report"
    : report.verification_state === "partially_supported"
      ? "Partially Supported"
      : report.verification_state === "not_supported"
        ? "Not Yet Sensor-Supported"
        : "Pending Sensor Confirmation";
}

export function categoryKind(category: FieldReportCategory) {
  return stationCategories.some((item) => item.value === category) ? "station" : "weather";
}
