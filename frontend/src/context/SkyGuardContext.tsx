import { createContext, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/endpoints";
import { ApiError } from "../api/client";
import { SkyGuardSocket } from "../api/websocket";
import type {
  AnomalyEvent,
  ConnectionStatus,
  DashboardSummary,
  DataMode,
  FieldReport,
  FieldReportCreatePayload,
  IngestPayload,
  NodeId,
  MonitoringContext,
  MaintenanceStatus,
  MLStatus,
  MqttStatus,
  SensorHealth,
  SensorReading,
  TrustedReading,
  VisionEvent,
  VisionStatus,
  WebSocketEnvelope,
} from "../api/types";
import { eventIdentity } from "../utils/format";

interface SkyGuardCommands {
  ingest: (payload: IngestPayload) => Promise<TrustedReading>;
  createFieldReport: (payload: FieldReportCreatePayload) => Promise<FieldReport>;
  updateFieldReport: (reportId: string, payload: Partial<Pick<FieldReport, "severity" | "observation" | "notes">>) => Promise<FieldReport>;
  resolveFieldReport: (reportId: string, notes?: string) => Promise<FieldReport>;
}

export interface SkyGuardContextValue {
  mode: DataMode;
  backendStatus: "checking" | "online" | "offline";
  mqttStatus: MqttStatus | null;
  socketStatus: ConnectionStatus;
  loading: boolean;
  commandPending: boolean;
  error: string | null;
  summary: DashboardSummary;
  histories: Record<NodeId, SensorReading[]>;
  events: AnomalyEvent[];
  health: SensorHealth[];
  trusted: TrustedReading[];
  fieldReports: FieldReport[];
  monitoringContexts: Record<NodeId, MonitoringContext>;
  visionEvents: VisionEvent[];
  visionStatuses: VisionStatus[];
  maintenance: MaintenanceStatus[];
  fieldNotice: { title: string; message: string } | null;
  clearFieldNotice: () => void;
  lastUpdatedAt: string | null;
  refresh: () => Promise<void>;
  commands: SkyGuardCommands;
}

export const SkyGuardContext = createContext<SkyGuardContextValue | null>(null);

const nodeIds: NodeId[] = ["AWS_001", "AWS_002", "AWS_003"];

const emptyHistories: Record<NodeId, SensorReading[]> = {
  AWS_001: [],
  AWS_002: [],
  AWS_003: [],
};

const emptySummary: DashboardSummary = {
  system: { status: "awaiting_data", detector_mode: "heuristic_nonfreeze_safety" },
  nodes: nodeIds.map((node_id) => ({
    node_id,
    status: "awaiting_data",
    health_score: 0,
    communication_quality: 0,
    communication_state: "awaiting_data",
    latest: null,
    active_anomalies: [],
  })),
  recent_events: [],
  metrics: {
    total_readings: 0,
    anomaly_count: 0,
    anomalies_by_type: {},
    active_nodes: 0,
    average_processing_latency_ms: 0,
    communication_states: { AWS_001: "awaiting_data", AWS_002: "awaiting_data", AWS_003: "awaiting_data" },
  },
};

function readingIdentity(reading: SensorReading) {
  return `${reading.node_id}:${reading.timestamp}`;
}

function mergeReadings(...groups: SensorReading[][]) {
  const readings = new Map<string, SensorReading>();
  groups.flat().forEach((reading) => readings.set(readingIdentity(reading), reading));
  return Array.from(readings.values())
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp))
    .slice(-200);
}

function mergeTrusted(...groups: TrustedReading[][]) {
  const readings = new Map<string, TrustedReading>();
  groups.flat().forEach((reading) => readings.set(readingIdentity(reading.raw), reading));
  return Array.from(readings.values())
    .sort((left, right) => Date.parse(left.raw.timestamp) - Date.parse(right.raw.timestamp))
    .slice(-300);
}

function isRealReading(reading: SensorReading | null | undefined): reading is SensorReading {
  return Boolean(reading && reading.source !== "simulator");
}

function stationIsOnline(state: string) {
  return !["awaiting_data", "communication_failure", "offline", "unknown"].includes(state);
}

function withStationMetrics(summary: DashboardSummary): DashboardSummary {
  const activeNodes = summary.nodes.filter((node) => node.latest && stationIsOnline(node.communication_state)).length;
  return {
    ...summary,
    system: { ...summary.system, stations_online: `${activeNodes} / ${summary.nodes.length}` },
    metrics: {
      ...summary.metrics,
      active_nodes: activeNodes,
      communication_states: Object.fromEntries(summary.nodes.map((node) => [node.node_id, node.communication_state])) as Record<NodeId, string>,
    },
  };
}

function upsertEvent(events: AnomalyEvent[], incoming: AnomalyEvent) {
  const identity = eventIdentity(incoming);
  return [incoming, ...events.filter((event) => eventIdentity(event) !== identity)].slice(0, 100);
}

export function SkyGuardProvider({ children }: { children: ReactNode }) {
  const [mode] = useState<DataMode>("live");
  const [backendStatus, setBackendStatus] = useState<"checking" | "online" | "offline">("checking");
  const [mqttStatus, setMqttStatus] = useState<MqttStatus | null>(null);
  const [socketStatus, setSocketStatus] = useState<ConnectionStatus>("disconnected");
  const [loading, setLoading] = useState(true);
  const [commandPending, setCommandPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState(emptySummary);
  const [histories, setHistories] = useState<Record<NodeId, SensorReading[]>>(emptyHistories);
  const [events, setEvents] = useState<AnomalyEvent[]>([]);
  const [health, setHealth] = useState<SensorHealth[]>([]);
  const [trusted, setTrusted] = useState<TrustedReading[]>([]);
  const [fieldReports, setFieldReports] = useState<FieldReport[]>([]);
  const [monitoringContexts, setMonitoringContexts] = useState<Record<NodeId, MonitoringContext>>({} as Record<NodeId, MonitoringContext>);
  const [visionEvents, setVisionEvents] = useState<VisionEvent[]>([]);
  const [visionStatuses, setVisionStatuses] = useState<VisionStatus[]>([]);
  const [maintenance, setMaintenance] = useState<MaintenanceStatus[]>([]);
  const [fieldNotice, setFieldNotice] = useState<{ title: string; message: string } | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const refreshInFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      let backendHealth;
      try {
        backendHealth = await api.getHealth();
      } catch (cause) {
        setBackendStatus("offline");
        setMqttStatus(null);
        setError(cause instanceof Error ? cause.message : "Backend unavailable");
        return;
      }

      setBackendStatus("online");
      setMqttStatus(backendHealth.mqtt);

      try {
        const [nextSummary, nextNodes, latestRows, historyRows, recordRows, nextEvents, nextHealth, nextReports, nextContexts, nextVisionEvents, nextVisionStatuses, nextMaintenance] = await Promise.all([
          api.getDashboardSummary(),
          api.getNodes(),
          Promise.all(nodeIds.map(async (nodeId) => {
            try {
              return await api.getLatest(nodeId);
            } catch (cause) {
              if (cause instanceof ApiError && cause.status === 404) return null;
              throw cause;
            }
          })),
          Promise.all(nodeIds.map((nodeId) => api.getHistory(nodeId, 200))),
          Promise.all(nodeIds.map((nodeId) => api.getRecords(nodeId, 200))),
          api.getEvents(100),
          api.getSensorHealth(),
          api.getFieldReports(),
          api.getMonitoringContexts(),
          api.getVisionEvents(100),
          api.getVisionStatus(),
          api.getMaintenance(),
        ]);
        const realLatestRows = latestRows.filter(isRealReading);
        const mergedSummary = withStationMetrics({
          ...nextSummary,
          nodes: nextSummary.nodes.map((node) => ({
            ...node,
            latest: realLatestRows.find((reading) => reading.node_id === node.node_id) ?? node.latest,
            communication_state: nextNodes.find((item) => item.node_id === node.node_id)?.communication_state ?? node.communication_state,
          })),
        });
        setSummary(mergedSummary);
        setEvents(nextEvents);
        setHealth(nextHealth);
        setTrusted((current) => mergeTrusted(
          current,
          recordRows.flatMap((response) => response.records).filter((record) => isRealReading(record.raw)),
        ));
        setHistories((current) => Object.fromEntries(nodeIds.map((nodeId, index) => [
          nodeId,
          mergeReadings(
            current[nodeId],
            historyRows[index].filter(isRealReading),
            isRealReading(latestRows[index]) ? [latestRows[index]] : [],
          ),
        ])) as Record<NodeId, SensorReading[]>);
        setFieldReports(nextReports);
        setMonitoringContexts(Object.fromEntries(nextContexts.map((context) => [context.node_id, context])) as Record<NodeId, MonitoringContext>);
        setVisionEvents(nextVisionEvents);
        setVisionStatuses(nextVisionStatuses);
        setMaintenance(nextMaintenance);
        setError(null);

        const latestRealTimestamp = latestRows
          .filter(isRealReading)
          .map((reading) => reading.received_at ?? reading.timestamp)
          .sort((left, right) => Date.parse(right) - Date.parse(left))[0];
        setLastUpdatedAt(backendHealth.mqtt.last_message_at ?? latestRealTimestamp ?? null);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Dashboard data could not be refreshed");
      }
    } finally {
      refreshInFlight.current = false;
      setLoading(false);
    }
  }, []);

  const handleSocketMessage = useCallback((message: WebSocketEnvelope) => {
    setBackendStatus("online");

    if (message.type === "sensor_reading") {
      const reading = message.data as SensorReading;
      if (!isRealReading(reading)) return;
      setLastUpdatedAt(reading.received_at ?? reading.timestamp);
      setHistories((current) => ({
        ...current,
        [reading.node_id]: mergeReadings(current[reading.node_id], [reading]),
      }));
      setSummary((current) => withStationMetrics({
        ...current,
        nodes: current.nodes.map((node) => (node.node_id === reading.node_id ? {
          ...node,
          latest: reading,
          communication_state: reading.communication_state ?? node.communication_state,
        } : node)),
        metrics: { ...current.metrics, total_readings: current.metrics.total_readings + 1 },
      }));
      return;
    }

    if (message.type === "trusted_reading") {
      const reading = message.data as TrustedReading;
      if (!isRealReading(reading.raw)) return;
      setTrusted((current) => mergeTrusted(current, [reading]));
      return;
    }

    if (["anomaly", "sensor_consistency", "recovery"].includes(message.type)) {
      const event = message.data as AnomalyEvent;
      setEvents((current) => upsertEvent(current, event));
      setSummary((current) => ({ ...current, recent_events: upsertEvent(current.recent_events, event).slice(0, 20) }));
      return;
    }

    if (message.type === "sensor_health") {
      const nextHealth = message.data as SensorHealth;
      setHealth((current) => [nextHealth, ...current.filter((item) => item.node_id !== nextHealth.node_id)]);
      setSummary((current) => withStationMetrics({
        ...current,
        nodes: current.nodes.map((node) =>
          node.node_id === nextHealth.node_id
            ? {
                ...node,
                status: nextHealth.status,
                health_score: nextHealth.node,
                communication_quality: nextHealth.communication_quality,
                communication_state: nextHealth.communication_state,
              }
            : node,
        ),
      }));
      return;
    }

    if (message.type === "system_status") {
      const status = message.data as {
        detector_mode?: string;
        ml_model?: MLStatus;
        mqtt?: MqttStatus;
      };
      if (status.mqtt) setMqttStatus(status.mqtt);
      setSummary((current) => ({
        ...current,
        system: {
          ...current.system,
          detector_mode: status.detector_mode ?? current.system.detector_mode,
          ml_model: status.ml_model ?? current.system.ml_model,
        },
      }));
      return;
    }

    if (message.type === "communication_status") {
      const status = message.data as {
        node_id: NodeId;
        state: string;
        communication_quality: number;
      };
      setSummary((current) => withStationMetrics({
        ...current,
        nodes: current.nodes.map((node) =>
          node.node_id === status.node_id
            ? { ...node, communication_state: status.state, communication_quality: status.communication_quality }
            : node,
        ),
      }));
      return;
    }

    if (["field_report_created", "field_report_updated", "field_report_resolved", "field_report_expired", "field_report_corroboration"].includes(message.type)) {
      const report = message.data as FieldReport;
      setFieldReports((current) => [report, ...current.filter((item) => item.id !== report.id)]);
      if (message.type === "field_report_created") {
        const station = report.nearby_stations[0];
        setFieldNotice({
          title: "Field report received",
          message: `${report.category.replace(/_/g, " ")} ${station ? `near ${station.node_id.replace("_", "-")}` : "recorded"}. Monitoring context updated.`,
        });
      } else if (message.type === "field_report_corroboration") {
        setFieldNotice({ title: "Sensor evidence updated", message: report.message });
      } else if (message.type === "field_report_resolved") {
        setFieldNotice({ title: "Field report resolved", message: "Nearby monitoring context has been recalculated." });
      }
      return;
    }

    if (message.type === "node_monitoring_context") {
      const context = message.data as MonitoringContext;
      setMonitoringContexts((current) => ({ ...current, [context.node_id]: context }));
      setSummary((current) => ({
        ...current,
        nodes: current.nodes.map((node) => node.node_id === context.node_id ? { ...node, monitoring_context: context } : node),
      }));
      return;
    }

    if (message.type === "vision_event") {
      const event = message.data as VisionEvent;
      setVisionEvents((current) => [event, ...current.filter((item) => item.id !== event.id)].slice(0, 100));
      return;
    }

    if (message.type === "vision_status") {
      const status = message.data as VisionStatus;
      setVisionStatuses((current) => [status, ...current.filter((item) => item.node_id !== status.node_id)]);
      return;
    }

    if (["peer_failover_started", "peer_failover_updated", "peer_failover_ended"].includes(message.type)) {
      void refresh();
      return;
    }

    if (message.type === "maintenance_status") {
      const payload = message.data as { node_id: NodeId; sensors: MaintenanceStatus[] };
      setMaintenance((current) => [...current.filter((item) => item.node_id !== payload.node_id), ...payload.sensors]);
    }
  }, [refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const interval = window.setInterval(() => void refresh(), socketStatus === "connected" ? 10_000 : 5_000);
    return () => window.clearInterval(interval);
  }, [refresh, socketStatus]);

  useEffect(() => {
    const socket = new SkyGuardSocket({ onMessage: handleSocketMessage, onStatus: setSocketStatus });
    socket.connect();
    return () => socket.stop();
  }, [handleSocketMessage]);

  const runCommand = useCallback(
    async <T,>(command: () => Promise<T>) => {
      setCommandPending(true);
      try {
        const result = await command();
        await refresh();
        return result;
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : "Command failed";
        setError(message);
        throw cause;
      } finally {
        setCommandPending(false);
      }
    },
    [refresh],
  );

  const commands = useMemo<SkyGuardCommands>(
    () => ({
      ingest: (payload) => runCommand(() => api.ingest(payload)),
      createFieldReport: (payload) => runCommand(() => api.createFieldReport(payload)),
      updateFieldReport: (reportId, payload) => runCommand(() => api.updateFieldReport(reportId, payload)),
      resolveFieldReport: (reportId, notes) => runCommand(() => api.resolveFieldReport(reportId, notes)),
    }),
    [runCommand],
  );

  const value = useMemo<SkyGuardContextValue>(
    () => ({
      mode,
      backendStatus,
      mqttStatus,
      socketStatus,
      loading,
      commandPending,
      error,
      summary,
      histories,
      events,
      health,
      trusted,
      fieldReports,
      monitoringContexts,
      visionEvents,
      visionStatuses,
      maintenance,
      fieldNotice,
      clearFieldNotice: () => setFieldNotice(null),
      lastUpdatedAt,
      refresh,
      commands,
    }),
    [
      mode,
      backendStatus,
      mqttStatus,
      socketStatus,
      loading,
      commandPending,
      error,
      summary,
      histories,
      events,
      health,
      trusted,
      fieldReports,
      monitoringContexts,
      visionEvents,
      visionStatuses,
      maintenance,
      fieldNotice,
      lastUpdatedAt,
      refresh,
      commands,
    ],
  );

  return <SkyGuardContext.Provider value={value}>{children}</SkyGuardContext.Provider>;
}
