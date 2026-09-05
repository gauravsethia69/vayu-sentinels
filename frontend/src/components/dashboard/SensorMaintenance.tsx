import { useEffect, useState } from "react"
import { ClipboardCheck, Database, Download, FileText, ShieldAlert } from "lucide-react"
import { api } from "../../api/endpoints"
import type { SensorSpec } from "../../api/types"
import { useSkyGuard } from "../../hooks/useSkyGuard"
import { displayNodeId, titleCase } from "../../utils/format"
import { EmptyState, PanelHeading, StatusPill } from "./StatusUi"

const localDatasheets = [
  { model: "DS18B20", title: "DS18B20 Temperature Sensor", href: "/datasheets/DS18B20_Datasheet.pdf", installed: true },
  { model: "DHT22", title: "DHT22 / AM2302 Temperature & Humidity", href: "/datasheets/DHT22_Datasheet.pdf", installed: true },
  { model: "BMP280", title: "BMP280 Pressure & Temperature Sensor", href: "/datasheets/BMP280_Datasheet.pdf", installed: true },
  { model: "ESP32-WROOM-32", title: "ESP32-WROOM-32 Controller", href: "/datasheets/ESP32_WROOM_32_Datasheet.pdf", installed: true },
  { model: "SW-420", title: "SW-420 Vibration Sensor Reference", href: "/datasheets/SW420_Vibration_Datasheet.pdf", installed: false },
  { model: "KY-038", title: "KY-038 Sound Sensor Reference", href: "/datasheets/KY038_Sound_Datasheet.pdf", installed: false },
] as const

export default function SensorMaintenance() {
  const { maintenance: rows } = useSkyGuard()
  const [specs, setSpecs] = useState<SensorSpec[]>([])
  useEffect(() => {
    void api.getSensorSpecs().then(setSpecs)
  }, [])
  const nodes = ["AWS_001", "AWS_002", "AWS_003"] as const
  const models = ["DS18B20", "DHT22", "BMP280"] as const
  return (
    <div className="dashboard-view-stack">
      <div className="view-toolbar">
        <div>
          <p className="panel-eyebrow">Transparent heuristic risk</p>
          <h2>Sensor Maintenance</h2>
          <span>
            Health, exposure, inspection, and verified datasheet limits inform
            priority. This is not Remaining Useful Life.
          </span>
        </div>
      </div>
      <section className="glass-card maintenance-matrix">
        <PanelHeading
          eyebrow="All installed sensors"
          title="Sensor health matrix"
        />
        {rows.length ? (
          <div className="maintenance-table">
            <div className="maintenance-row head">
              <strong>Sensor</strong>
              {nodes.map((node) => (
                <strong key={node}>{displayNodeId(node)}</strong>
              ))}
            </div>
            {models.map((model) => (
              <div className="maintenance-row" key={model}>
                <strong>{model}</strong>
                {nodes.map((node) => {
                  const item = rows.find(
                    (row) => row.node_id === node && row.model === model,
                  )
                  return (
                    <div key={node}>
                      <b>{item ? Math.round(item.health_score) : "—"}</b>
                      <StatusPill
                        state={item?.maintenance_priority ?? "unavailable"}
                      />
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No inventory"
            message="The backend creates sensor inventory additively at startup."
          />
        )}
      </section>
      <div className="maintenance-card-grid">
        {rows.map((item) => (
          <article className="glass-card maintenance-card" key={item.sensor_id}>
            <span className="metric-icon">
              <ShieldAlert size={17} />
            </span>
            <p>{displayNodeId(item.node_id)}</p>
            <h3>{item.model}</h3>
            <StatusPill state={item.maintenance_priority} />
            <ul>
              {item.reasons.slice(0, 3).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            <small>{item.recommended_action}</small>
          </article>
        ))}
      </div>
      <section className="glass-card spec-registry">
        <PanelHeading
          eyebrow="Verified specification registry"
          title="Installed sensor specifications"
        />
        {specs.map((spec) => {
          const local = localDatasheets.find((item) => item.model === spec.model)
          return (
            <article key={spec.model}>
              <Database size={16} />
              <div>
                <strong>
                  {spec.model} · {spec.manufacturer}
                </strong>
                <span>{spec.accuracy_specification}</span>
                <small>
                  Measurement range {spec.measurement_temperature_min_c} to{" "}
                  {spec.measurement_temperature_max_c}°C · Manufacturer lifetime not
                  specified.
                </small>
              </div>
              <a href={local?.href ?? spec.datasheet_reference} target="_blank" rel="noreferrer">
                <ClipboardCheck size={15} />
                Datasheet
              </a>
            </article>
          )
        })}
      </section>

      <section className="glass-card datasheet-library">
        <PanelHeading
          eyebrow="Offline admin references"
          title="Sensor datasheet library"
        />
        <p className="datasheet-library-note">
          These PDFs are bundled with the dashboard, so the administrator can open
          manufacturer/reference documentation even when internet access is unavailable.
        </p>
        <div className="datasheet-card-grid">
          {localDatasheets.map((item) => (
            <article className="datasheet-card" key={item.model}>
              <span className="datasheet-icon"><FileText size={18} /></span>
              <div>
                <strong>{item.model}</strong>
                <span>{item.title}</span>
                <small>{item.installed ? "Used in current SkyGuard AWS prototype" : "Additional hardware reference"}</small>
              </div>
              <div className="datasheet-actions">
                <a href={item.href} target="_blank" rel="noreferrer">
                  <ClipboardCheck size={14} /> View PDF
                </a>
                <a href={item.href} download>
                  <Download size={14} /> Download
                </a>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
