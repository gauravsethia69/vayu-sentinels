import { ArrowRight, CloudSun, RadioTower, ShieldCheck } from "lucide-react"
import { useNavigate } from "react-router-dom"
import { useSkyGuard } from "../hooks/useSkyGuard"
import { formatValue } from "../utils/format"

export default function EntryPage() {
  const navigate = useNavigate()
  const { backendStatus, summary } = useSkyGuard()
  const station = summary.nodes[0]
  return (
    <main className="entry-page">
      <div className="atmosphere-grid" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      <header className="entry-brand">
        <span>S</span>
        <div>
          <strong>SkyGuard AI</strong>
          <small>Resilient Weather Intelligence Network</small>
        </div>
        <em className={backendStatus}>
          {backendStatus === "online" ? "Network live" : "Backend offline"}
        </em>
      </header>
      <section className="entry-hero">
        <p>How would you like to use SkyGuard?</p>
        <h1>
          One Weather Network.
          <br />
          Two Ways to Experience It.
        </h1>
        <span>
          Explore live environmental conditions as a citizen, or manage the
          intelligence behind the network as an AWS administrator.
        </span>
      </section>
      <section className="role-grid">
        <article className="role-card public-role">
          <div className="role-icon">
            <CloudSun />
          </div>
          <p>PUBLIC USER</p>
          <h2>Public Weather Portal</h2>
          <span>
            See nearby weather station data and contribute local observations.
          </span>
          <div className="role-preview">
            <div>
              <small>Nearby Station</small>
              <strong>AWS-001</strong>
            </div>
            <div>
              <small>Temperature</small>
              <strong>
                {formatValue(station?.latest?.temperature_c, "°C", 1)}
              </strong>
            </div>
            <div>
              <small>Humidity</small>
              <strong>
                {formatValue(station?.latest?.humidity_pct, "%", 0)}
              </strong>
            </div>
            <div>
              <small>Status</small>
              <strong>
                {backendStatus === "online" && station?.latest ? "Normal" : "Unavailable"}
              </strong>
            </div>
          </div>
          <button onClick={() => navigate("/public")}>
            Enter Public Portal <ArrowRight size={17} />
          </button>
        </article>
        <div className="entry-network" aria-hidden="true">
          <span />
          <RadioTower />
          <span />
        </div>
        <article className="role-card admin-role">
          <div className="role-icon">
            <ShieldCheck />
          </div>
          <p>AWS ADMIN</p>
          <h2>AWS Management Console</h2>
          <span>
            Monitor sensors, diagnose anomalies and operate the SkyGuard
            network.
          </span>
          <div className="role-preview">
            <div>
              <small>Stations Online</small>
              <strong>
                {summary.nodes.filter((node) => node.latest).length}
              </strong>
            </div>
            <div>
              <small>System Health</small>
              <strong>
                {summary.nodes.length
                  ? Math.round(
                      summary.nodes.reduce(
                        (sum, node) => sum + node.health_score,
                        0,
                      ) / summary.nodes.length,
                    )
                  : 0}
                %
              </strong>
            </div>
            <div>
              <small>Active Alerts</small>
              <strong>
                {summary.nodes.reduce(
                  (sum, node) => sum + node.active_anomalies.length,
                  0,
                )}
              </strong>
            </div>
            <div>
              <small>Trusted Stream</small>
              <strong>
                {backendStatus === "online" ? "Online" : "Offline"}
              </strong>
            </div>
          </div>
          <button onClick={() => navigate("/admin/login")}>
            Admin Login <ArrowRight size={17} />
          </button>
        </article>
      </section>
      <footer>
        Powered by real-time AWS sensor intelligence and community observations.
      </footer>
    </main>
  )
}
