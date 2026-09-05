import {
  CheckCircle2,
  Cloud,
  CloudRain,
  LocateFixed,
  RadioTower,
  Send,
  Wind,
} from "lucide-react"
import { useState, type FormEvent } from "react"
import { Link } from "react-router-dom"
import { api } from "../api/endpoints"
import type {
  FieldReport,
  FieldReportCategory,
  FieldReportSeverity,
  NodeId,
  ReporterConfidence,
} from "../api/types"
import PublicShell from "../components/public/PublicShell"
import { titleCase } from "../utils/format"

const quick: Array<[FieldReportCategory, string, typeof Cloud]> = [
  ["clouds_approaching", "Dark Clouds", Cloud],
  ["light_rain", "Rain Starting", CloudRain],
  ["heavy_rain", "Heavy Rain", CloudRain],
  ["strong_wind", "Strong Wind", Wind],
  ["fog", "Fog", Cloud],
  ["thunderstorm", "Thunderstorm", CloudRain],
  ["flooding", "Flooding", CloudRain],
  ["sensor_damage", "AWS Issue", RadioTower],
]
const categories = [
  "clouds_approaching",
  "light_rain",
  "heavy_rain",
  "strong_wind",
  "thunderstorm",
  "fog",
  "low_visibility",
  "hail",
  "dust_storm",
  "rapid_weather_change",
  "flooding",
  "other_weather",
  "sensor_damage",
  "sensor_obstruction",
  "communication_issue",
  "power_issue",
  "custom_observation",
] as FieldReportCategory[]
export default function PublicReportPage() {
  const [category, setCategory] =
    useState<FieldReportCategory>("clouds_approaching")
  const [observation, setObservation] = useState("")
  const [scope, setScope] = useState<NodeId | "both" | "unknown">("AWS_001")
  const [direction, setDirection] = useState("")
  const [severity, setSeverity] = useState<FieldReportSeverity>("moderate")
  const [confidence, setConfidence] = useState<ReporterConfidence>("medium")
  const [location, setLocation] = useState("Near AWS-001")
  const [coordinates, setCoordinates] = useState<{
    latitude: number
    longitude: number
  } | null>(null)
  const [submitted, setSubmitted] = useState<FieldReport | null>(null)
  const [error, setError] = useState("")
  const [pending, setPending] = useState(false)
  function prefill(value: FieldReportCategory, label: string) {
    setCategory(value)
    setObservation(
      value === "sensor_damage"
        ? "The nearby weather station appears to be damaged or obstructed."
        : `${label} observed near the local weather station.`,
    )
  }
  function locate() {
    navigator.geolocation?.getCurrentPosition(
      (p) => {
        setCoordinates({
          latitude: p.coords.latitude,
          longitude: p.coords.longitude,
        })
        setLocation("Current browser location")
      },
      () =>
        setError(
          "Location permission was not granted. You can still submit using a station location.",
        ),
    )
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    setPending(true)
    setError("")
    try {
      const station_scope: NodeId[] =
        scope === "both"
          ? ["AWS_001", "AWS_002", "AWS_003"]
          : scope === "unknown"
            ? []
            : [scope]
      setSubmitted(
        await api.createPublicReport({
          category,
          observation,
          station_scope,
          direction: direction || null,
          severity,
          reporter_confidence: confidence,
          location_label: location,
          radius_km: 10,
          expires_in_minutes: 60,
          ...(coordinates || {}),
        }),
      )
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Report submission failed",
      )
    } finally {
      setPending(false)
    }
  }
  if (submitted)
    return (
      <PublicShell>
        <main className="report-confirmation">
          <CheckCircle2 />
          <p>REPORT RECEIVED</p>
          <h1>{titleCase(submitted.category)}</h1>
          <span>{submitted.location_label}</span>
          <strong>Under Verification</strong>
          <p>
            SkyGuard will compare this observation with nearby AWS sensor
            trends. It does not alter station readings or detector output.
          </p>
          <Link to="/public/reports">View Community Reports</Link>
        </main>
      </PublicShell>
    )
  return (
    <PublicShell>
      <main className="public-report-page">
        <header>
          <p>COMMUNITY OBSERVATION</p>
          <h1>What are you observing?</h1>
          <span>
            Your report provides context. SkyGuard independently compares it
            with nearby sensor evidence.
          </span>
        </header>
        <section className="quick-public-reports">
          {quick.map(([value, label, Icon]) => (
            <button
              key={value}
              className={category === value ? "active" : ""}
              onClick={() => prefill(value, label)}
            >
              <Icon />
              <span>{label}</span>
            </button>
          ))}
        </section>
        <form className="public-report-form" onSubmit={(e) => void submit(e)}>
          <label>
            <span>Category</span>
            <select
              value={category}
              onChange={(e) =>
                setCategory(e.target.value as FieldReportCategory)
              }
            >
              {categories.map((v) => (
                <option key={v} value={v}>
                  {titleCase(v)}
                </option>
              ))}
            </select>
          </label>
          <label className="wide">
            <span>Observation description</span>
            <textarea
              minLength={10}
              required
              value={observation}
              onChange={(e) => setObservation(e.target.value)}
              placeholder="Dark clouds are approaching rapidly from the west and wind appears to be increasing."
            />
          </label>
          <label>
            <span>Nearby station</span>
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as typeof scope)}
            >
                <option value="AWS_001">AWS-001</option>
                <option value="AWS_002">AWS-002</option>
                <option value="AWS_003">AWS-003</option>
                <option value="both">All / Area-wide</option>
              <option value="unknown">Not Sure</option>
            </select>
          </label>
          <label>
            <span>Direction</span>
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value)}
            >
              <option value="">Not Sure</option>
              {[
                "north",
                "north-east",
                "east",
                "south-east",
                "south",
                "south-west",
                "west",
                "north-west",
              ].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Severity</span>
            <select
              value={severity}
              onChange={(e) =>
                setSeverity(e.target.value as FieldReportSeverity)
              }
            >
              {["information", "low", "moderate", "high", "critical"].map(
                (v) => (
                  <option key={v}>{v}</option>
                ),
              )}
            </select>
          </label>
          <label>
            <span>User confidence</span>
            <select
              value={confidence}
              onChange={(e) =>
                setConfidence(e.target.value as ReporterConfidence)
              }
            >
              {["low", "medium", "high"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <label className="wide">
            <span>Location description</span>
            <div className="location-input">
              <input
                value={location}
                onChange={(e) => setLocation(e.target.value)}
              />
              <button type="button" onClick={locate}>
                <LocateFixed />
                Use My Location
              </button>
            </div>
          </label>
          {error && <p className="form-error">{error}</p>}
          <button className="submit-public-report" disabled={pending}>
            <Send />
            {pending ? "Submitting…" : "Submit Field Report"}
          </button>
        </form>
      </main>
    </PublicShell>
  )
}
