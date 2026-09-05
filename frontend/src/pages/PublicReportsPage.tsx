import { CloudRain, RadioTower } from "lucide-react";
import { useMemo, useState } from "react";
import PublicShell from "../components/public/PublicShell";
import { useSkyGuard } from "../hooks/useSkyGuard";
import { formatRelativeTime, titleCase } from "../utils/format";

export default function PublicReportsPage() {
  const { fieldReports } = useSkyGuard();
  const [filter, setFilter] = useState("active");
  const reports = useMemo(() => fieldReports.filter((report) => report.source === "public_user" && (filter === "all" || report.status === filter)), [fieldReports, filter]);
  return <PublicShell><main className="community-page">
    <header><p>COMMUNITY INTELLIGENCE</p><h1>Reports from around the network</h1><span>Observations stay clearly separated from independently measured sensor evidence.</span></header>
    <div className="community-filters">{["active","resolved","expired","all"].map((value) => <button className={filter === value ? "active" : ""} onClick={() => setFilter(value)} key={value}>{titleCase(value)}</button>)}</div>
    <section className="community-list">{reports.map((report) => <article key={report.id}>
      <span className="community-icon">{report.category.includes("sensor") || report.category.includes("communication") ? <RadioTower /> : <CloudRain />}</span>
      <div><p>PUBLIC REPORT</p><h2>{titleCase(report.category)}</h2><span>{report.observation}</span><small>{report.location_label || "Prototype station area"} · {formatRelativeTime(report.created_at)}</small></div>
      <aside><strong>{report.verification_state === "corroborated" ? "Sensor Supported" : titleCase(report.verification_state)}</strong><span>{report.corroboration_confidence}% sensor corroboration</span><em>{titleCase(report.status)}</em></aside>
    </article>)}{!reports.length && <div className="community-empty">No {filter === "all" ? "community" : filter} reports.</div>}</section>
  </main></PublicShell>;
}
