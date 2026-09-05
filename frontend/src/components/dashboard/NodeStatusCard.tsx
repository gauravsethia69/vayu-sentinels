import { memo } from "react";
import { ChevronRight, CloudSun, RadioTower, Wrench } from "lucide-react";
import { Line, LineChart, ResponsiveContainer } from "recharts";
import type { NodeSummary, SensorReading } from "../../api/types";
import { displayNodeId, formatRelativeTime, formatValue, titleCase } from "../../utils/format";
import { StatusPill } from "./StatusUi";

interface NodeStatusCardProps {
  node: NodeSummary;
  history: SensorReading[];
  selected: boolean;
  onSelect: () => void;
}

function NodeStatusCard({ node, history, selected, onSelect }: NodeStatusCardProps) {
  const latest = node.latest;
  const sparkline = history.slice(-24).map((reading) => ({ value: reading.temperature_c }));
  const fieldContext = node.monitoring_context?.active_context[0];

  return (
    <article
      className={`glass-card node-status-card ${selected ? "selected" : ""}`}
    >
      <div className="node-card-head">
        <div className="node-identity">
          <span className="node-icon"><RadioTower size={17} /></span>
          <div>
            <p className="panel-eyebrow">Automatic Weather Station</p>
            <h3>{displayNodeId(node.node_id)}</h3>
          </div>
        </div>
        <StatusPill state={latest ? node.communication_state : "awaiting_data"} />
      </div>

      <div className="node-values">
        <div><span>Temperature</span><strong>{formatValue(latest?.temperature_c, "°C")}</strong></div>
        <div><span>Humidity</span><strong>{formatValue(latest?.humidity_pct, "%")}</strong></div>
        <div><span>Pressure</span><strong>{formatValue(latest?.pressure_hpa, " hPa")}</strong></div>
      </div>

      <div className="node-sparkline" aria-label="Recent temperature trend">
        {sparkline.length > 1 ? (
          <ResponsiveContainer width="100%" height={54}>
            <LineChart data={sparkline}>
              <Line type="natural" dataKey="value" stroke="#3269AB" strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : <span>No recent trend</span>}
      </div>

      <div className="node-card-meta">
        <div>
          <span>Health</span>
          <strong>{Math.round(node.health_score)}%</strong>
        </div>
        <div>
          <span>Communication</span>
          <strong>{titleCase(node.communication_state)}</strong>
          <small>{Math.round(node.communication_quality)}% link quality</small>
        </div>
        <div>
          <span>Last packet</span>
          <strong>{formatRelativeTime(latest?.received_at ?? latest?.timestamp)}</strong>
          <small>{titleCase(latest?.source ?? "source unavailable")}</small>
        </div>
      </div>

      {fieldContext && (
        <div className={`node-field-context ${fieldContext.type.includes("sensor") || fieldContext.type.includes("issue") ? "station" : "weather"}`}>
          {fieldContext.type.includes("sensor") || fieldContext.type.includes("issue") ? <Wrench size={13} /> : <CloudSun size={13} />}
          <span><strong>Field Context</strong>{titleCase(fieldContext.type)} nearby · {titleCase(node.monitoring_context?.monitoring_mode)}</span>
        </div>
      )}

      <button className="text-command" onClick={onSelect} aria-label={`View ${displayNodeId(node.node_id)} details`}>
        View details <ChevronRight size={15} aria-hidden="true" />
      </button>
    </article>
  );
}

export default memo(
  NodeStatusCard,
  (previous, next) =>
    previous.node === next.node
    && previous.history === next.history
    && previous.selected === next.selected,
);
