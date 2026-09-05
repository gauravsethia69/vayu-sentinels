import {
  AlertCircle,
  CloudSun,
  Droplets,
  Gauge,
  MapPin,
  RadioTower,
  Thermometer,
  Users,
} from "lucide-react"
import { Link } from "react-router-dom"
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import PublicShell from "../components/public/PublicShell"
import { useSkyGuard } from "../hooks/useSkyGuard"
import {
  displayNodeId,
  formatRelativeTime,
  formatValue,
  titleCase,
} from "../utils/format"

function publicState(state?: string, hasReading = true) {
  if (!hasReading) return "Data Temporarily Unavailable";
  if (!state || state === "healthy") return "Operating Normally"
  if (state.includes("communication")) return "Data Temporarily Unavailable"
  if (state.includes("environment")) return "Weather Changing"
  return "Readings Being Verified"
}
export default function PublicPortal() {
  const { backendStatus, fieldReports, histories, summary } = useSkyGuard()
  const nearest = summary.nodes[0]
  const latest = nearest?.latest
  const chart = histories.AWS_001.slice(-30).map((r) => ({
    time: new Date(r.timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    }),
    temperature: r.temperature_c,
    humidity: r.humidity_pct,
  }))
  const activeReports = fieldReports.filter(
    (r) => r.source === "public_user" && r.status === "active",
  )
  return (
    <PublicShell>
      <main className="public-page">
        {backendStatus !== "online" && (
          <div className="public-offline">
            <AlertCircle size={16} />
            Backend Offline · deterministic demo data is clearly labeled where
            shown
          </div>
        )}
        <section className="public-weather-hero">
          <div>
            <p>WEATHER AROUND YOU</p>
            <h1>{formatValue(latest?.temperature_c, "°C", 1)}</h1>
          <h2>{publicState(nearest?.status, Boolean(latest))}</h2>
            <span>
              <MapPin size={15} />
              Nearest prototype station ·{" "}
              {displayNodeId(nearest?.node_id ?? "AWS_001")}
            </span>
          </div>
          <div className="weather-measures">
            <div>
              <Droplets />
              <span>
                Humidity
                <strong>{formatValue(latest?.humidity_pct, "%", 0)}</strong>
              </span>
            </div>
            <div>
              <Gauge />
              <span>
                Pressure
                <strong>{formatValue(latest?.pressure_hpa, " hPa", 0)}</strong>
              </span>
            </div>
            <div>
              <RadioTower />
              <span>
                Updated<strong>{formatRelativeTime(latest?.timestamp)}</strong>
              </span>
            </div>
          </div>
        </section>
        <section className="public-actions">
          <div>
            <CloudSun />
            <span>
              <strong>Nearby conditions</strong>
              <small>
                {summary.nodes.length} stations in the prototype network
              </small>
            </span>
          </div>
          <div>
            <Users />
            <span>
              <strong>{activeReports.length} active observations</strong>
              <small>Community intelligence nearby</small>
            </span>
          </div>
          <Link to="/public/report">Report Local Condition</Link>
        </section>
        <section id="stations" className="public-section">
          <header>
            <p>NEARBY AWS NETWORK</p>
            <h2>Live station conditions</h2>
          </header>
          <div className="public-station-grid">
            {summary.nodes.map((node) => (
              <article key={node.node_id}>
                <div>
                  <RadioTower />
                  <strong>{displayNodeId(node.node_id)}</strong>
                <em>{publicState(node.status, Boolean(node.latest))}</em>
                </div>
                <dl>
                  <div>
                    <dt>Temperature</dt>
                    <dd>{formatValue(node.latest?.temperature_c, "°C", 1)}</dd>
                  </div>
                  <div>
                    <dt>Humidity</dt>
                    <dd>{formatValue(node.latest?.humidity_pct, "%", 0)}</dd>
                  </div>
                  <div>
                    <dt>Pressure</dt>
                    <dd>{formatValue(node.latest?.pressure_hpa, " hPa", 0)}</dd>
                  </div>
                </dl>
                <small>
                  Last update {formatRelativeTime(node.latest?.timestamp)}
                </small>
                {node.monitoring_context?.active_context[0] && (
                  <span className="field-context-badge">
                    Community context ·{" "}
                    {titleCase(node.monitoring_context.active_context[0].type)}
                  </span>
                )}
              </article>
            ))}
          </div>
        </section>
        <section className="public-section public-trend">
          <header>
            <p>LOCAL TREND</p>
            <h2>Recent weather movement</h2>
          </header>
          <div className="public-chart">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart}>
                <CartesianGrid stroke="#dceaf2" vertical={false} />
                <XAxis dataKey="time" tickLine={false} axisLine={false} />
                <YAxis tickLine={false} axisLine={false} width={36} />
                <Tooltip />
                <Line
                  type="natural"
                  dataKey="temperature"
                  stroke="#176b9c"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="natural"
                  dataKey="humidity"
                  stroke="#1aa6a6"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="trend-legend">
            <span>
              <Thermometer />
              Temperature
            </span>
            <span>
              <Droplets />
              Humidity
            </span>
          </div>
        </section>
        <section className="public-community">
          <div>
            <p>LOCAL COMMUNITY INTELLIGENCE</p>
            <h2>
              {activeReports.length
                ? `${activeReports.length} active observation${
                    activeReports.length === 1 ? "" : "s"
                  } nearby`
                : "No active community observations"}
            </h2>
            {activeReports[0] && (
              <span>
                {titleCase(activeReports[0].category)} ·{" "}
                {activeReports[0].message}
              </span>
            )}
          </div>
          <Link to="/public/reports">View Community Reports</Link>
        </section>
      </main>
    </PublicShell>
  )
}
