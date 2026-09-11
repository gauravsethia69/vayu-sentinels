import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { NODE_IDS, type NodeId, type Parameter, type SensorReading, type TrustedReading } from "../../api/types";
import { EmptyState, PanelHeading } from "./StatusUi";

const fieldByParameter: Record<Parameter, keyof SensorReading> = {
  temperature: "temperature_c",
  humidity: "humidity_pct",
  pressure: "pressure_hpa",
};

const units: Record<Parameter, string> = { temperature: "°C", humidity: "%", pressure: " hPa" };
const bands: Record<Parameter, [number, number]> = {
  temperature: [20, 40],
  humidity: [35, 90],
  pressure: [980, 1030],
};
const nodeColors: Record<NodeId, string> = {
  AWS_001: "#3269AB",
  AWS_002: "#82B5F0",
  AWS_003: "#14B8A6",
  AWS_004: "#8B5CF6",
  AWS_005: "#F59E0B",
};

interface TrendChartProps {
  histories: Record<NodeId, SensorReading[]>;
  trusted: TrustedReading[];
  parameter: Parameter;
  selectedNode?: NodeId;
  compact?: boolean;
}

export default function TrendChart({ histories, trusted, parameter, selectedNode = "AWS_001", compact = false }: TrendChartProps) {
  const data = useMemo(() => {
    const nodeRows = NODE_IDS.map((nodeId) => histories[nodeId].slice(compact ? -24 : -60));
    const trustedNode = trusted.filter((item) => item.raw.node_id === selectedNode).slice(compact ? -24 : -60);
    const count = Math.max(...nodeRows.map((rows) => rows.length), trustedNode.length);
    const key = fieldByParameter[parameter];

    return Array.from({ length: count }, (_, index) => {
      const readings = nodeRows.map((rows) => rows[rows.length - count + index]);
      const trustedPoint = trustedNode[trustedNode.length - count + index];
      const timestamp = readings.find(Boolean)?.timestamp ?? trustedPoint?.trusted.timestamp;
      const parsed = timestamp ? Date.parse(timestamp) : Number.NaN;
      const trustedValue = trustedPoint?.trusted[key];
      const row: Record<string, string | number | null> = {
        timestamp: timestamp ?? "",
        timeValue: Number.isNaN(parsed) ? index : parsed,
        time: Number.isNaN(parsed) ? `${index + 1}` : new Date(parsed).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
        trusted: typeof trustedValue === "number" ? trustedValue : null,
      };
      NODE_IDS.forEach((nodeId, nodeIndex) => {
        const value = readings[nodeIndex]?.[key];
        row[nodeId] = typeof value === "number" ? value : null;
      });
      return row;
    });
  }, [compact, histories, parameter, selectedNode, trusted]);



  if (!data.length) {
    return (
      <div className="glass-card chart-panel">
        <PanelHeading eyebrow="Historical telemetry" title={`${parameter[0].toUpperCase()}${parameter.slice(1)} trend`} />
        <EmptyState title="No history yet" message="Ingest live sensor readings to populate this chart." />
      </div>
    );
  }

  const [bandStart, bandEnd] = bands[parameter];
  return (
    <div className="glass-card chart-panel">
      <PanelHeading
        eyebrow={compact ? "Live trend" : "Historical telemetry"}
        title={`${parameter[0].toUpperCase()}${parameter.slice(1)} comparison`}
        action={<span className="chart-unit">{units[parameter].trim()}</span>}
      />
      <div className={compact ? "chart-wrap compact" : "chart-wrap"}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 12, right: 10, left: compact ? -24 : -12, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="rgba(169,206,255,0.34)" strokeDasharray="3 5" />
            <ReferenceArea y1={bandStart} y2={bandEnd} fill="#82B5F0" fillOpacity={0.055} />
            <XAxis
              dataKey="timeValue"
              type="number"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(value) => new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              tick={{ fontSize: 12, fill: "#82B5F0" }}
              axisLine={false}
              tickLine={false}
              minTickGap={28}
            />
            <YAxis tick={{ fontSize: 12, fill: "#82B5F0" }} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
            <Tooltip
              formatter={(value) => [typeof value === "number" ? `${value.toFixed(parameter === "pressure" ? 1 : 2)}${units[parameter]}` : "—"]}
              labelFormatter={(value) => new Date(Number(value)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              contentStyle={{ background: "rgba(255,255,255,0.97)", border: "1px solid rgba(198,224,255,0.7)", borderRadius: 12, boxShadow: "0 8px 24px rgba(50,105,171,0.12)", fontSize: 14 }}
            />
            {!compact && <Legend wrapperStyle={{ fontSize: 13, color: "#1F4F82" }} />}
            {NODE_IDS.map((nodeId) => (
              <Line key={nodeId} type="natural" dataKey={nodeId} name={nodeId.replace("_", "-")} stroke={nodeColors[nodeId]} strokeWidth={2} dot={false} connectNulls animationDuration={1100} />
            ))}
            <Line type="natural" dataKey="trusted" name={`Trusted ${selectedNode.replace("_", "-")}`} stroke="#16a34a" strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls animationDuration={1100} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
