import type { AnomalyEvent, NodeId, SensorReading } from "../api/types";

export function displayNodeId(nodeId: string) {
  return nodeId.replace("_", "-");
}

export function titleCase(value?: string | null) {
  if (!value) return "Unavailable";
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatValue(value: number | null | undefined, unit = "", digits = 1) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}${unit}` : "—";
}

export function formatRelativeTime(timestamp?: string | null, now = Date.now()) {
  if (!timestamp) return "Unavailable";
  const parsed = Date.parse(timestamp);
  if (Number.isNaN(parsed)) return "Unavailable";
  const seconds = Math.max(0, Math.round((now - parsed) / 1000));
  if (seconds < 5) return "Just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return new Date(parsed).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatEventTime(timestamp: string) {
  const parsed = Date.parse(timestamp);
  return Number.isNaN(parsed) ? "—" : new Date(parsed).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function isEnvironmentalEvent(event: AnomalyEvent) {
  const value = `${event.event_type} ${event.anomaly_type} ${event.message}`.toLowerCase();
  return value.includes("environment") || value.includes("weather_change") || value.includes("weather change");
}

export function eventIdentity(event: AnomalyEvent) {
  return `${event.node_id}:${event.timestamp}:${event.event_type}:${event.anomaly_type}:${event.parameter ?? "none"}`;
}

export function latestForNode(readings: SensorReading[], nodeId: NodeId) {
  return [...readings].reverse().find((reading) => reading.node_id === nodeId) ?? null;
}
