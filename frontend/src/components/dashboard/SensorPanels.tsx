import { ArrowDown, CheckCircle2, CircleAlert, Gauge, ShieldCheck, Thermometer, Waves } from "lucide-react";
import type { AnomalyEvent, NodeId, NodeSummary, SensorHealth, TrustedReading } from "../../api/types";
import { displayNodeId, formatRelativeTime, formatValue, titleCase } from "../../utils/format";
import { EmptyState, PanelHeading, StatusPill } from "./StatusUi";

interface SensorDetailPanelProps {
  selectedNode: NodeId;
  nodes: NodeSummary[];
  health: SensorHealth[];
  trusted: TrustedReading[];
}

export function SensorDetailPanel({ selectedNode, nodes, health, trusted }: SensorDetailPanelProps) {
  const node = nodes.find((item) => item.node_id === selectedNode);
  const reading = node?.latest;
  const nodeHealth = health.find((item) => item.node_id === selectedNode);
  const trustedReading = [...trusted].reverse().find((item) => item.raw.node_id === selectedNode)?.trusted;

  if (!reading) {
    return (
      <div className="glass-card detail-panel">
        <PanelHeading eyebrow="Node detail" title={`${displayNodeId(selectedNode)} sensor breakdown`} />
        <EmptyState title="No sensor packet" message="This node has not supplied a reading yet." />
      </div>
    );
  }

  const raw = reading.raw_sensors ?? reading.sensors ?? {};
  const sensorCards = [
    {
      name: "DS18B20",
      role: "Primary temperature",
      icon: Thermometer,
      values: [{ label: "Temperature", value: formatValue(raw.ds18b20_temperature_c ?? reading.primary_temperature_c, "°C", 2) }],
      health: nodeHealth?.temperature,
      status: nodeHealth?.sensor_states.temperature,
      missing: raw.ds18b20_temperature_c == null && reading.primary_temperature_c == null,
    },
    {
      name: "DHT22",
      role: "Temperature + humidity",
      icon: Waves,
      values: [
        { label: "Temperature", value: formatValue(raw.dht22_temperature_c, "°C", 2) },
        { label: "Humidity", value: formatValue(raw.dht22_humidity_pct ?? reading.humidity_pct, "%") },
      ],
      health: nodeHealth ? Math.min(nodeHealth.temperature, nodeHealth.humidity) : undefined,
      status: nodeHealth?.sensor_states.humidity,
      missing: raw.dht22_temperature_c == null && raw.dht22_humidity_pct == null,
    },
    {
      name: "BMP280",
      role: "Pressure + temperature",
      icon: Gauge,
      values: [
        { label: "Temperature", value: formatValue(raw.bmp280_temperature_c ?? raw.barometric_temperature_c ?? raw.bmp180_temperature_c, "°C", 2) },
        { label: "Pressure", value: formatValue(raw.bmp280_pressure_hpa ?? raw.pressure_hpa ?? raw.bmp180_pressure_hpa ?? reading.pressure_hpa, " hPa") },
      ],
      health: nodeHealth?.pressure,
      status: nodeHealth?.sensor_states.pressure,
      missing: raw.bmp280_temperature_c == null && raw.bmp180_temperature_c == null && raw.bmp280_pressure_hpa == null && raw.bmp180_pressure_hpa == null,
    },
  ];

  return (
    <section className="glass-card detail-panel">
      <PanelHeading
        eyebrow="Node detail"
        title={`${displayNodeId(selectedNode)} sensor breakdown`}
        action={<StatusPill state={node?.status ?? "unavailable"} />}
      />

      <div className="sensor-card-grid">
        {sensorCards.map((sensor) => {
          const Icon = sensor.icon;
          return (
            <article className={`sensor-channel-card ${sensor.missing ? "missing" : ""}`} key={sensor.name}>
              <div className="sensor-channel-head">
                <span><Icon size={16} aria-hidden="true" /></span>
                <div><strong>{sensor.name}</strong><small>{sensor.role}</small></div>
              </div>
              <div className="sensor-values-list">
                {sensor.values.map((value) => <div key={value.label}><span>{value.label}</span><strong>{value.value}</strong></div>)}
              </div>
              <div className="sensor-health-line">
                <span>{sensor.missing ? "Unavailable" : titleCase(sensor.status ?? "not reported")}</span>
                <strong>{sensor.health == null ? "—" : `${Math.round(sensor.health)}%`}</strong>
              </div>
            </article>
          );
        })}
      </div>

      <div className="consensus-strip">
        <div><span>Temperature consensus</span><strong>{formatValue(reading.temperature_consensus_c, "°C", 2)}</strong></div>
        <div><span>Reference temperature</span><strong>{formatValue(reading.reference_temperature_c, "°C", 2)}</strong></div>
        <div className="trusted"><span>Trusted temperature</span><strong>{formatValue(trustedReading?.temperature_c, "°C", 2)}</strong></div>
      </div>
      <div className="trusted-meta-grid">
        <div><span>Data source</span><strong>{titleCase(reading.source ?? "unavailable")}</strong></div>
        <div><span>Raw quality</span><strong>{titleCase(reading.quality ?? "raw")}</strong></div>
        <div><span>Wi-Fi RSSI</span><strong>{formatValue(reading.device?.wifi_rssi, " dBm", 0)}</strong></div>
        <div><span>Last received</span><strong>{formatRelativeTime(reading.received_at ?? reading.timestamp)}</strong></div>
      </div>
    </section>
  );
}

interface TrustedDataPanelProps {
  selectedNode: NodeId;
  trusted: TrustedReading[];
  events: AnomalyEvent[];
  expanded?: boolean;
}

export function TrustedDataPanel({ selectedNode, trusted, events, expanded = false }: TrustedDataPanelProps) {
  const nodeRows = trusted.filter((item) => item.raw.node_id === selectedNode);
  const latest = nodeRows.at(-1);

  if (!latest) {
    return (
      <section className="glass-card trusted-panel">
        <PanelHeading eyebrow="Validated pipeline" title="Raw vs trusted data" />
        <EmptyState title="No trusted stream" message="Trusted output appears after the first sensor reading." />
      </section>
    );
  }

  const rawSensors = latest.raw.raw_sensors ?? latest.raw.sensors ?? {};
  const corrected = latest.trusted.corrected_parameters ?? [];
  const relatedEvent = corrected.includes("temperature")
    ? events.find(
        (event) => event.node_id === selectedNode && event.parameter === "temperature" && event.corrected_value != null,
      )
    : undefined;
  const failover = latest.trusted.peer_failover;
  const sourceNodes = latest.trusted.source_nodes ?? failover?.source_nodes ?? [];
  const excludedNode = latest.trusted.excluded_node ?? failover?.excluded_node;
  const correctionEvent = events.find(
    (event) => event.node_id === selectedNode && corrected.includes(event.parameter ?? "") && event.corrected_value != null,
  );
  const rows = [
    ["DS18B20", rawSensors.ds18b20_temperature_c ?? latest.raw.temperature_c],
    ["DHT22", rawSensors.dht22_temperature_c],
    ["BMP280", rawSensors.bmp280_temperature_c ?? rawSensors.barometric_temperature_c ?? rawSensors.bmp180_temperature_c],
  ] as const;

  return (
    <section className={`glass-card trusted-panel ${expanded ? "expanded" : ""}`}>
      <PanelHeading
        eyebrow="Validated pipeline"
        title="Raw vs trusted data"
        action={<StatusPill state={latest.trusted.quality ?? "unavailable"} />}
      />
      <div className="trusted-flow">
        <div className="raw-stack">
          <p className="flow-label">Raw sensor data</p>
          {rows.map(([name, value]) => {
            const suspected = relatedEvent?.suspected_sensor?.toLowerCase().includes(name.toLowerCase());
            return (
              <div className={`raw-row ${suspected ? "suspected" : ""}`} key={name}>
                <span>{name}{suspected && <CircleAlert size={13} aria-label="Suspected sensor" />}</span>
                <strong>{formatValue(value, "°C", 2)}</strong>
              </div>
            );
          })}
        </div>
        <span className="flow-arrow"><ArrowDown size={19} aria-hidden="true" /></span>
        <div className="trusted-output">
          <ShieldCheck size={20} aria-hidden="true" />
          <span>Trusted temperature</span>
          <strong>{formatValue(latest.trusted.temperature_c, "°C", 2)}</strong>
          <small>{titleCase(latest.trusted.provenance_detail ?? latest.trusted.provenance)}</small>
        </div>
      </div>

      <div className="trusted-meta-grid">
        <div><span>Raw retained</span><strong><CheckCircle2 size={14} /> Yes</strong></div>
        <div><span>Quality</span><strong>{titleCase(latest.trusted.quality)}</strong></div>
        <div><span>Corrected parameters</span><strong>{corrected.length ? corrected.map(titleCase).join(", ") : "None"}</strong></div>
        <div><span>Provenance</span><strong>{titleCase(latest.trusted.provenance_detail ?? latest.trusted.provenance)}</strong></div>
        <div><span>Correction confidence</span><strong>{failover ? `${failover.confidence}%` : correctionEvent ? `${correctionEvent.confidence}%` : "Not reported"}</strong></div>
        <div><span>Suspected sensor</span><strong>{relatedEvent?.suspected_sensor ?? "Not identified"}</strong></div>
        <div><span>Source nodes</span><strong>{sourceNodes.length ? sourceNodes.map(displayNodeId).join(", ") : "Local sensors"}</strong></div>
        <div><span>Excluded node</span><strong>{excludedNode ? displayNodeId(excludedNode) : "None"}</strong></div>
        <div><span>Data source</span><strong>{titleCase(latest.raw.source ?? "unavailable")}</strong></div>
      </div>

      <div className="consensus-strip">
        {([
          ["Temperature", "temperature_c", "°C"],
          ["Humidity", "humidity_pct", "%"],
          ["Pressure", "pressure_hpa", " hPa"],
        ] as const).map(([label, key, unit]) => (
          <div className={corrected.includes(label.toLowerCase()) ? "trusted" : ""} key={key}>
            <span>{label} · raw → trusted</span>
            <strong>{formatValue(latest.raw[key], unit, 2)} → {formatValue(latest.trusted[key], unit, 2)}</strong>
          </div>
        ))}
      </div>

      {failover && (
        <div className="peer-failover-card">
          <div><p className="flow-label">Peer failover active</p><strong>{displayNodeId(selectedNode)} · {titleCase(failover.parameter)}</strong><small>{failover.reason}</small></div>
          <div className="peer-failover-flow"><span>{failover.source_nodes.map(displayNodeId).join(" + ")}</span><ArrowDown size={17}/><strong>{formatValue(failover.trusted_value, failover.parameter === "pressure" ? " hPa" : failover.parameter === "humidity" ? "%" : "°C", 2)}</strong></div>
        </div>
      )}

      {expanded && (
        <div className="trusted-history">
          <p className="flow-label">Recent trusted values</p>
          <div className="trusted-history-table">
            {nodeRows.slice(-8).reverse().map((entry) => (
              <div key={`${entry.raw.timestamp}-${entry.raw.node_id}`}>
                <time>{new Date(entry.raw.timestamp).toLocaleTimeString()}</time>
                <span>{formatValue(entry.raw.temperature_c, "°C", 2)}</span>
                <strong>{formatValue(entry.trusted.temperature_c, "°C", 2)}</strong>
                <em>{titleCase(entry.trusted.provenance)}</em>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
