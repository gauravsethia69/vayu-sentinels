import { useState, useEffect, useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Area, AreaChart
} from "recharts";
import { useSkyGuard } from "../hooks/useSkyGuard";
import { formatValue, titleCase } from "../utils/format";

const detectionFeatures = [
  { icon: "⚡", title: "Sudden Spike", desc: "Detects instantaneous sensor value jumps far outside normal operating range.", wide: false },
  { icon: "❄️", title: "Sensor Freeze", desc: "Uses the trained Random Forest with 30–60 second temporal and reference-sensor features to identify a sustained temperature freeze.", wide: false },
  { icon: "📈", title: "Gradual Drift", desc: "Tracks slow divergence from peer sensors indicating calibration loss over time.", wide: true },
  { icon: "💾", title: "Sensor Corruption", desc: "Catches physically impossible or out-of-range encoded values from faulty hardware.", wide: false },
  { icon: "📡", title: "Data Loss", desc: "Flags missing packets and sensor silence across expected reporting windows.", wide: false },
  { icon: "🔌", title: "Communication Failure", desc: "Detects broken or degraded Wi-Fi links with extended packet gaps and RSSI anomalies.", wide: false },
  { icon: "⚖️", title: "Cross-Sensor Disagreement", desc: "Compares DS18B20, DHT22, and BMP280 readings for internal node consensus.", wide: false },
  { icon: "🏔️", title: "Station-to-Station Inconsistency", desc: "Cross-validates AWS-001, AWS-002, and AWS-003 to isolate localized vs. environmental changes.", wide: true },
  { icon: "📉", title: "Sensor Degradation", desc: "Tracks gradual health score decline indicating approaching hardware maintenance needs.", wide: false },
  { icon: "🌤️", title: "Genuine Weather Change", desc: "Confirms real environmental events by requiring coherent multi-sensor, multi-station agreement.", wide: false },
];

const stackItems = {
  hardware: [
    { icon: "🔲", label: "ESP32 DevKit V1", sub: "Dual-core 240MHz MCU" },
    { icon: "🌡️", label: "DS18B20", sub: "Primary temperature" },
    { icon: "💧", label: "DHT22", sub: "Temperature + humidity" },
    { icon: "🔵", label: "BMP280", sub: "Pressure + temperature" },
    { icon: "🖥️", label: "OLED Display", sub: "Live status output" },
    { icon: "💡", label: "LED Indicators", sub: "Visual fault signaling" },
    { icon: "🔘", label: "Fault Buttons", sub: "Live telemetry fault injection" },
  ],
  software: [
    { icon: "⚡", label: "FastAPI", sub: "Async REST + WebSocket backend" },
    { icon: "🗃️", label: "SQLite", sub: "Lightweight time-series storage" },
    { icon: "🔄", label: "WebSockets", sub: "Real-time bidirectional data" },
    { icon: "🧪", label: "Hardware Fault Testing", sub: "5 AWS-001 physical test modes" },
    { icon: "✅", label: "Trusted Data Pipeline", sub: "Cross-sensor consensus output" },
    { icon: "🧠", label: "Anomaly Detector", sub: "Hybrid Random Forest v6 + safety engine" },
    { icon: "📐", label: "ML-Ready Architecture", sub: "Pluggable model interface" },
  ],
};

const impacts = [
  { icon: "🌦️", title: "Weather Forecasting", desc: "Higher confidence environmental inputs improve forecast accuracy at regional stations." },
  { icon: "🚨", title: "Disaster Preparedness", desc: "Validated sensor data prevents false alarms and missed early-warning signals." },
  { icon: "🔧", title: "Sensor Maintenance", desc: "Early drift detection enables targeted maintenance before full sensor failure." },
  { icon: "📡", title: "Remote AWS Monitoring", desc: "Deployed in hard-to-reach locations, SkyGuard reduces costly manual inspection trips." },
  { icon: "✅", title: "Data Quality Assurance", desc: "Trusted stream output ensures downstream models and reports use verified values." },
];

interface HomePageProps {
  onOpenDashboard: () => void;
}

export default function HomePage({ onOpenDashboard }: HomePageProps) {
  const { backendStatus, histories, socketStatus, summary, trusted } = useSkyGuard();
  const [activeNavSection, setActiveNavSection] = useState("overview");
  const [chartTab, setChartTab] = useState("temperature");
  const [faultScenario, setFaultScenario] = useState("Spike");
  const [navScrolled, setNavScrolled] = useState(false);

  useEffect(() => {
    const handler = () => setNavScrolled(window.scrollY > 40);
    window.addEventListener("scroll", handler);
    return () => window.removeEventListener("scroll", handler);
  }, []);

  const navLinks = ["Overview", "How It Works", "Technology", "Hardware Testing", "Impact"];
  const awsOne = summary.nodes.find((node) => node.node_id === "AWS_001");
  const awsTwo = summary.nodes.find((node) => node.node_id === "AWS_002");
  const activeAnomalies = summary.nodes.flatMap((node) => node.active_anomalies);
  const averageHealth = summary.nodes.length
    ? Math.round(summary.nodes.reduce((total, node) => total + node.health_score, 0) / summary.nodes.length)
    : 0;
  const streamConnected = socketStatus === "connected";
  const previewLabel = backendStatus === "online" ? "Live System" : "Backend Offline · Last Known Data";
  const streamLabel = streamConnected ? "Connected" : backendStatus === "online" ? "Reconnecting" : "Offline";
  const temperatureChartData = useMemo(() => {
    const first = histories.AWS_001.slice(-20);
    const second = histories.AWS_002.slice(-20);
    const trustedRows = trusted.filter((row) => row.raw.node_id === "AWS_001").slice(-20);
    const count = Math.max(first.length, second.length, trustedRows.length);
    return Array.from({ length: count }, (_, index) => {
      const one = first[first.length - count + index];
      const two = second[second.length - count + index];
      const trustedPoint = trustedRows[trustedRows.length - count + index];
      const timestamp = one?.timestamp ?? two?.timestamp ?? trustedPoint?.trusted.timestamp;
      return {
        t: timestamp ? new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : `${index + 1}`,
        aws1: one?.temperature_c,
        aws2: two?.temperature_c,
        trusted: trustedPoint?.trusted.temperature_c,
      };
    });
  }, [histories, trusted]);
  const secondaryChartData = useMemo(
    () => histories.AWS_001.slice(-20).map((reading) => ({
      t: new Date(reading.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      humidity: reading.humidity_pct,
      pressure: reading.pressure_hpa,
    })),
    [histories],
  );

  return (
    <div className="min-h-screen" style={{ fontFamily: "var(--font-body)" }}>
      {/* ── FLOATING NAV ─────────────────────────────────────────── */}
      <header
        className="fixed top-4 left-1/2 z-50 transition-all duration-300"
        style={{ transform: "translateX(-50%)", width: "min(900px, 94vw)" }}
      >
        <nav
          className="glass flex items-center justify-between px-5 py-3"
          style={{
            borderRadius: 99,
            boxShadow: navScrolled ? "0 8px 40px rgba(50,105,171,0.18)" : "0 4px 20px rgba(50,105,171,0.10)",
          }}
        >
          {/* Logo */}
          <div className="flex items-center gap-2">
            <div
              className="w-8 h-8 flex items-center justify-center text-white text-sm font-bold"
              style={{ background: "linear-gradient(135deg,#3269AB,#5792D7)", borderRadius: 10 }}
            >
              S
            </div>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 17, color: "#102A4A" }}>
              SkyGuard AI
            </span>
          </div>

          {/* Links */}
          <div className="hidden md:flex items-center gap-1">
            {navLinks.map((l) => (
              <a
                key={l}
                href={`#${l.toLowerCase().replace(/\s+/g, "-")}`}
                className="px-3 py-1.5 text-sm font-medium transition-all duration-200 rounded-full"
                style={{ color: "#1F4F82", textDecoration: "none" }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background = "rgba(50,105,171,0.08)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = "transparent";
                }}
              >
                {l}
              </a>
            ))}
          </div>

          {/* CTA */}
          <button
            onClick={onOpenDashboard}
            className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-white transition-all duration-200"
            style={{
              background: "linear-gradient(135deg,#3269AB,#5792D7)",
              borderRadius: 99,
              boxShadow: "0 2px 12px rgba(50,105,171,0.35)",
              border: "none",
              cursor: "pointer",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 20px rgba(50,105,171,0.5)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.boxShadow = "0 2px 12px rgba(50,105,171,0.35)";
            }}
          >
            Open Dashboard
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M2 6h8M7 3l3 3-3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        </nav>
      </header>

      {/* ── HERO ─────────────────────────────────────────────────── */}
      <section
        id="overview"
        className="relative min-h-screen flex flex-col items-center justify-center px-6 pt-28 pb-20 overflow-hidden"
        style={{
          background: "linear-gradient(170deg, #F8FBFF 0%, #EEF6FF 40%, #E0EEFF 70%, #D9EBFF 100%)",
        }}
      >
        {/* Atmospheric background grid */}
        <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ opacity: 0.35 }}>
          <defs>
            <pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">
              <path d="M 48 0 L 0 0 0 48" fill="none" stroke="rgba(82,146,215,0.25)" strokeWidth="0.5"/>
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#grid)" />
        </svg>

        {/* Soft glow blobs */}
        <div className="absolute pointer-events-none" style={{
          top: "20%", left: "15%", width: 500, height: 500,
          background: "radial-gradient(circle, rgba(130,181,240,0.18) 0%, transparent 70%)", borderRadius: "50%"
        }} />
        <div className="absolute pointer-events-none" style={{
          bottom: "15%", right: "10%", width: 400, height: 400,
          background: "radial-gradient(circle, rgba(198,224,255,0.25) 0%, transparent 70%)", borderRadius: "50%"
        }} />

        {/* Hero headline */}
        <div className="relative z-10 text-center max-w-3xl mx-auto mb-12">
          <div
            className="inline-flex items-center gap-2 px-3 py-1.5 mb-6 text-xs font-semibold uppercase tracking-widest"
            style={{
              background: "rgba(50,105,171,0.08)",
              border: "1px solid rgba(50,105,171,0.2)",
              borderRadius: 99,
              color: "#3269AB",
            }}
          >
            <span className="relative flex h-1.5 w-1.5">
              <span className="animate-ping-soft absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-green-500" />
            </span>
            Smart India Hackathon · {previewLabel}
          </div>

          <h1
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "clamp(44px, 6.2vw, 76px)",
              fontWeight: 700,
              letterSpacing: "0",
              lineHeight: 1.05,
              color: "#102A4A",
            }}
          >
            Trusted Weather Intelligence,
            <br />
            <span
              style={{
                background: "linear-gradient(135deg, #3269AB, #5792D7, #82B5F0)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
                backgroundClip: "text",
              }}
            >
              Even When Sensors Fail.
            </span>
          </h1>

          <p
            className="mt-6 text-base md:text-lg leading-relaxed mx-auto"
            style={{ color: "#1F4F82", maxWidth: 560, fontWeight: 400 }}
          >
            SkyGuard AI distinguishes genuine environmental changes from faulty Automatic Weather Station sensor behavior in real time.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-3 mt-8">
            <button
              onClick={onOpenDashboard}
              className="flex items-center gap-2 px-6 py-3 font-semibold text-white transition-all duration-200"
              style={{
                background: "linear-gradient(135deg,#3269AB,#5792D7)",
                borderRadius: 14,
                border: "none",
                cursor: "pointer",
                fontSize: 17,
                boxShadow: "0 4px 20px rgba(50,105,171,0.4)",
              }}
            >
              Open Live Dashboard
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 7h10M8 3l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            <a
              href="#how-it-works"
              className="flex items-center gap-2 px-6 py-3 font-semibold transition-all duration-200"
              style={{
                background: "rgba(255,255,255,0.9)",
                border: "1px solid rgba(198,224,255,0.8)",
                borderRadius: 14,
                color: "#1F4F82",
                textDecoration: "none",
                fontSize: 17,
                cursor: "pointer",
                boxShadow: "0 2px 12px rgba(50,105,171,0.08)",
              }}
            >
              Explore How It Works
            </a>
          </div>
        </div>

        {/* Central dashboard panel with floating cards */}
        <div className="relative z-10 w-full max-w-5xl mx-auto">
          <div className="relative flex justify-center">
            {/* Floating cards left */}
            <div className="absolute left-0 top-8 flex flex-col gap-3 z-20 hidden lg:flex">
              <FloatingStatCard label="AWS-001 Temperature" value={formatValue(awsOne?.latest?.temperature_c, "°C")} sub="DS18B20 Primary" status={!awsOne?.latest ? "idle" : awsOne.status === "healthy" ? "healthy" : "warning"} delay={0} />
              <FloatingStatCard label="AWS-002 Humidity" value={formatValue(awsTwo?.latest?.humidity_pct, "%")} sub="DHT22 Sensor" status={!awsTwo?.latest ? "idle" : awsTwo.status === "healthy" ? "healthy" : "warning"} delay={0.2} />
            </div>

            {/* Main panel */}
            <div className="glass-card p-5 w-full max-w-2xl" style={{ minHeight: 280 }}>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "#82B5F0" }}>{backendStatus === "online" ? "Live Temperature Feed" : "Last Known Temperature Feed"}</p>
                  <p style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 18, color: "#102A4A" }}>
                    AWS-001 vs AWS-002
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full" style={{ background: streamConnected ? "rgba(34,197,94,0.1)" : "rgba(217,119,6,0.1)", color: streamConnected ? "#16a34a" : "#b45309" }}>
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: streamConnected ? "#16a34a" : "#d97706" }} />
                    {streamConnected ? "Live" : "Reconnecting"}
                  </div>
                  <div className="px-2 py-1 rounded-lg text-xs font-semibold" style={{ background: activeAnomalies.length ? "rgba(239,68,68,0.1)" : "rgba(22,163,74,0.1)", color: activeAnomalies.length ? "#dc2626" : "#15803d" }}>
                    {activeAnomalies.length ? titleCase(activeAnomalies[0]).toUpperCase() : "NO ACTIVE FAULTS"}
                  </div>
                </div>
              </div>

              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={temperatureChartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(169,206,255,0.3)" />
                  <XAxis dataKey="t" tick={{ fontSize: 13, fill: "#82B5F0" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 13, fill: "#82B5F0" }} axisLine={false} tickLine={false} domain={[27, 37]} />
                  <Tooltip
                    contentStyle={{ background: "rgba(255,255,255,0.95)", border: "1px solid rgba(198,224,255,0.6)", borderRadius: 12, fontSize: 14 }}
                  />
                  <Line type="natural" dataKey="aws1" stroke="#3269AB" strokeWidth={2} dot={false} name="AWS-001" />
                  <Line type="natural" dataKey="aws2" stroke="#82B5F0" strokeWidth={2} dot={false} name="AWS-002" />
                  <Line type="natural" dataKey="trusted" stroke="#16a34a" strokeWidth={2} dot={false} strokeDasharray="4 2" name="Trusted" />
                </LineChart>
              </ResponsiveContainer>

              <div className="flex items-center gap-4 mt-3">
                {[["#3269AB","AWS-001"],["#82B5F0","AWS-002"],["#16a34a","Trusted Output"]].map(([color, label]) => (
                  <div key={label} className="flex items-center gap-1.5 text-xs" style={{ color: "#1F4F82" }}>
                    <div className="w-6 h-0.5" style={{ background: color }} />
                    {label}
                  </div>
                ))}
              </div>
            </div>

            {/* Floating cards right */}
            <div className="absolute right-0 top-8 flex flex-col gap-3 z-20 hidden lg:flex">
              <FloatingStatCard label="System Health" value={`${averageHealth}%`} sub={titleCase(summary.system.status)} status={summary.system.status === "healthy" ? "healthy" : "warning"} delay={0.1} />
              <FloatingStatCard label="Active Alerts" value={`${activeAnomalies.length}`} sub={activeAnomalies.length ? titleCase(activeAnomalies[0]) : "No active faults"} status={activeAnomalies.length ? "warning" : "healthy"} delay={0.3} />
              <FloatingStatCard label="Trusted Stream" value={streamLabel} sub={streamConnected ? "WebSocket active" : backendStatus === "online" ? "Reconnecting" : "Backend offline"} status={streamConnected ? "online" : "warning"} delay={0.4} />
            </div>
          </div>

          {/* Mobile floating cards row */}
          <div className="flex gap-3 mt-4 overflow-x-auto pb-2 lg:hidden">
            {[
              { label: "System Health", value: `${averageHealth}%`, status: summary.system.status },
              { label: "Active Alerts", value: `${activeAnomalies.length}`, status: activeAnomalies.length ? "warning" : "healthy" },
              { label: "Trusted Stream", value: streamLabel, status: streamConnected ? "online" : "warning" },
            ].map(c => (
              <div key={c.label} className="glass-card px-4 py-3 flex-shrink-0">
                <p className="text-xs font-medium" style={{ color: "#82B5F0" }}>{c.label}</p>
                <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 20, color: "#102A4A" }}>{c.value}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CORE VALUE ──────────────────────────────────────────────── */}
      <section
        className="py-24 px-6"
        style={{ background: "linear-gradient(180deg, #F0F7FF 0%, #F8FBFF 100%)" }}
      >
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-14">
            <SectionLabel>Core Insight</SectionLabel>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(31px, 4vw, 47px)", fontWeight: 600, color: "#102A4A", letterSpacing: "0" }}>
              Know whether the weather changed —<br />or the sensor did.
            </h2>
          </div>

          <div className="grid md:grid-cols-3 gap-5 relative">
            {/* Connector line */}
            <div className="hidden md:block absolute top-1/2 left-0 right-0 h-px" style={{ background: "linear-gradient(90deg, transparent, rgba(130,181,240,0.4), transparent)", transform: "translateY(-50%)" }} />

            {[
              {
                emoji: "🌤️",
                label: "REAL WEATHER",
                color: "#3269AB",
                bg: "rgba(50,105,171,0.06)",
                title: "Coherent Multi-Station Movement",
                desc: "All three stations and redundant sensors move coherently. All three sensor types support the change. SkyGuard classifies this as a genuine environmental event.",
              },
              {
                emoji: "⚠️",
                label: "SENSOR FAULT",
                color: "#d97706",
                bg: "rgba(217,119,6,0.06)",
                title: "Isolated Sensor Divergence",
                desc: "One sensor behaves differently from references and nearby stations. DS18B20 disagrees with DHT22 and BMP280. AWS-002 and AWS-003 remain stable. Anomaly confirmed.",
              },
              {
                emoji: "✅",
                label: "TRUSTED OUTPUT",
                color: "#16a34a",
                bg: "rgba(22,163,74,0.06)",
                title: "Reliable Estimated Value",
                desc: "The system selects or estimates a reliable environmental value using cross-sensor consensus. The trusted stream carries only verified data downstream.",
              },
            ].map((card) => (
              <div
                key={card.label}
                className="glass-card p-7 relative transition-all duration-300 group"
                style={{ background: "rgba(255,255,255,0.85)" }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.transform = "translateY(-4px)";
                  (e.currentTarget as HTMLElement).style.boxShadow = "0 12px 40px rgba(50,105,171,0.15)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.transform = "";
                  (e.currentTarget as HTMLElement).style.boxShadow = "";
                }}
              >
                <div className="w-12 h-12 rounded-2xl flex items-center justify-center text-2xl mb-5" style={{ background: card.bg }}>
                  {card.emoji}
                </div>
                <p className="text-xs font-bold uppercase tracking-widest mb-2" style={{ color: card.color }}>
                  {card.label}
                </p>
                <h3 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 19, color: "#102A4A", marginBottom: 8 }}>
                  {card.title}
                </h3>
                <p style={{ fontSize: 16, color: "#1F4F82", lineHeight: 1.6 }}>{card.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── HOW IT WORKS ────────────────────────────────────────────── */}
      <section id="how-it-works" className="py-24 px-6" style={{ background: "#F8FBFF" }}>
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-16">
            <SectionLabel>Architecture</SectionLabel>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(31px, 4vw, 43px)", fontWeight: 600, color: "#102A4A", letterSpacing: "0" }}>
              How SkyGuard Works
            </h2>
            <p className="mt-3 text-base" style={{ color: "#1F4F82" }}>
              Two redundant AWS nodes feed a multi-layer detection and correction pipeline.
            </p>
          </div>

          {/* Pipeline flow */}
          <div className="glass-card p-8">
            {/* Node cards */}
            <div className="grid md:grid-cols-2 gap-6 mb-10">
              {["001", "002"].map((id) => {
                const node = summary.nodes.find((item) => item.node_id.endsWith(id));
                const receiving = backendStatus === "online" && Boolean(node?.latest) && node?.communication_state !== "communication_failure";
                return (
                <div key={id} className="rounded-2xl p-5" style={{ background: "linear-gradient(135deg, rgba(50,105,171,0.06), rgba(130,181,240,0.08))", border: "1px solid rgba(198,224,255,0.5)" }}>
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-9 h-9 rounded-xl flex items-center justify-center text-white text-sm font-bold" style={{ background: "linear-gradient(135deg,#3269AB,#5792D7)" }}>
                      {id}
                    </div>
                    <div>
                      <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 17, color: "#102A4A" }}>AWS-{id}</p>
                      <p className="text-xs" style={{ color: "#82B5F0" }}>Automatic Weather Station</p>
                    </div>
                    <div
                      className="ml-auto flex items-center gap-1.5 text-xs font-semibold px-2 py-1 rounded-full"
                      style={{
                        background: receiving ? "rgba(34,197,94,0.1)" : "rgba(50,105,171,0.08)",
                        color: receiving ? "#16a34a" : "#3269AB",
                      }}
                    >
                      <span className={`w-1.5 h-1.5 rounded-full ${receiving ? "bg-green-500" : "bg-blue-400"}`} />
                      {receiving ? "Receiving" : "Awaiting data"}
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {[
                      { name: "DS18B20", role: "Primary temperature", icon: "🌡️" },
                      { name: "DHT22", role: "Temp + humidity", icon: "💧" },
                      { name: "BMP280", role: "Pressure + temp", icon: "🔵" },
                    ].map((s) => (
                      <div key={s.name} className="rounded-xl p-2.5 text-center" style={{ background: "rgba(255,255,255,0.7)", border: "1px solid rgba(198,224,255,0.4)" }}>
                        <div className="text-base mb-1">{s.icon}</div>
                        <p className="text-xs font-semibold" style={{ color: "#102A4A" }}>{s.name}</p>
                        <p className="text-xs" style={{ color: "#82B5F0", fontSize: 12 }}>{s.role}</p>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 flex items-center gap-2 text-xs" style={{ color: "#5792D7" }}>
                    <span>ESP32 DevKit V1</span>
                    <span className="opacity-40">·</span>
                    <span>Wi-Fi MQTT → FastAPI</span>
                  </div>
                </div>
              )})}
            </div>

            {/* Pipeline steps */}
            <div className="flex flex-wrap justify-center items-center gap-2">
              {["ESP32 Hardware", "FastAPI Backend", "Anomaly Engine", "Trusted Stream", "Dashboard"].map((step, i) => (
                <div key={step} className="flex items-center gap-2">
                  <div className="px-4 py-2 rounded-xl text-sm font-semibold" style={{ background: "rgba(50,105,171,0.08)", border: "1px solid rgba(50,105,171,0.15)", color: "#1F4F82" }}>
                    {step}
                  </div>
                  {i < 4 && (
                    <svg width="20" height="14" viewBox="0 0 20 14" fill="none">
                      <path d="M1 7h15M12 2l6 5-6 5" stroke="#82B5F0" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── DETECTION CAPABILITIES ──────────────────────────────────── */}
      <section id="technology" className="py-24 px-6" style={{ background: "linear-gradient(180deg, #F0F7FF, #F8FBFF)" }}>
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-14">
            <SectionLabel>Detection Engine</SectionLabel>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(31px, 4vw, 43px)", fontWeight: 600, color: "#102A4A", letterSpacing: "0" }}>
              What SkyGuard Detects
            </h2>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {detectionFeatures.map((f, i) => (
              <div
                key={f.title}
                className="glass-card p-5 transition-all duration-300 cursor-default"
                style={{ gridColumn: f.wide ? "span 2" : "span 1" }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.transform = "translateY(-3px)";
                  (e.currentTarget as HTMLElement).style.boxShadow = "0 12px 32px rgba(50,105,171,0.14)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.transform = "";
                  (e.currentTarget as HTMLElement).style.boxShadow = "";
                }}
              >
                <div className="text-2xl mb-3">{f.icon}</div>
                <h3 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 17, color: "#102A4A", marginBottom: 6 }}>
                  {f.title}
                </h3>
                <p style={{ fontSize: 15, color: "#1F4F82", lineHeight: 1.55 }}>{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── EXPLAINABILITY SHOWCASE ──────────────────────────────────── */}
      <section className="py-24 px-6" style={{ background: "#F8FBFF" }}>
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-14">
            <SectionLabel>Explainable Detection</SectionLabel>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(31px, 4vw, 43px)", fontWeight: 600, color: "#102A4A", letterSpacing: "0" }}>
              Not just detection — explanation.
            </h2>
            <p className="mt-3 text-base" style={{ color: "#1F4F82" }}>
              Every anomaly comes with verifiable evidence and a recommended action.
            </p>
          </div>

          <div className="glass-card p-8 max-w-3xl mx-auto">
            {/* Alert header */}
            <div className="flex items-start justify-between mb-6">
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <div className="px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider" style={{ background: "rgba(239,68,68,0.1)", color: "#dc2626", border: "1px solid rgba(239,68,68,0.2)" }}>
                    ⚠ Sensor Drift Detected
                  </div>
                </div>
                <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 24, color: "#102A4A" }}>
                  Temperature Sensor Drift
                </p>
                <p className="text-sm mt-1" style={{ color: "#5792D7" }}>AWS-001 · DS18B20 · Detected 15:21</p>
              </div>
              <div className="text-right">
                <p className="text-xs font-semibold uppercase tracking-wider mb-1" style={{ color: "#82B5F0" }}>Confidence</p>
                <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 38, color: "#3269AB" }}>94%</p>
              </div>
            </div>

            {/* Values row */}
            <div className="grid grid-cols-3 gap-4 mb-6">
              {[
                { label: "Raw Reading", value: "35.1°C", color: "#dc2626", bg: "rgba(239,68,68,0.06)" },
                { label: "Expected", value: "29.4°C", color: "#3269AB", bg: "rgba(50,105,171,0.06)" },
                { label: "Trusted Output", value: "29.3°C", color: "#16a34a", bg: "rgba(22,163,74,0.06)" },
              ].map((v) => (
                <div key={v.label} className="rounded-2xl p-4 text-center" style={{ background: v.bg, border: `1px solid ${v.color}22` }}>
                  <p className="text-xs font-semibold uppercase tracking-wide mb-2" style={{ color: v.color, opacity: 0.8 }}>{v.label}</p>
                  <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 24, color: v.color }}>{v.value}</p>
                </div>
              ))}
            </div>

            {/* Evidence */}
            <div className="rounded-2xl p-5 mb-5" style={{ background: "rgba(50,105,171,0.04)", border: "1px solid rgba(198,224,255,0.5)" }}>
              <p className="text-xs font-bold uppercase tracking-wider mb-3" style={{ color: "#5792D7" }}>Evidence</p>
              <ul className="space-y-2">
                {[
                  "DS18B20 differs from DHT22 by +5.8°C",
                  "BMP280 confirms reference temperature at 29.4°C",
                  "AWS-002 remains stable — environmental change excluded",
                  "Deviation increased gradually across 11 consecutive readings",
                ].map((e) => (
                  <li key={e} className="flex items-start gap-2.5 text-sm" style={{ color: "#1F4F82" }}>
                    <span className="mt-0.5 text-blue-500">•</span>
                    {e}
                  </li>
                ))}
              </ul>
            </div>

            {/* Recommended action */}
            <div className="flex items-center gap-3 rounded-xl px-4 py-3" style={{ background: "rgba(217,119,6,0.06)", border: "1px solid rgba(217,119,6,0.2)" }}>
              <span className="text-amber-500 text-lg">🔧</span>
              <div>
                <p className="text-xs font-bold uppercase tracking-wide" style={{ color: "#d97706" }}>Recommended Action</p>
                <p className="text-sm mt-0.5" style={{ color: "#78350f" }}>Inspect or recalibrate the DS18B20 temperature sensor on AWS-001.</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── ANALYTICS PREVIEW ──────────────────────────────────────── */}
      <section className="py-24 px-6" style={{ background: "linear-gradient(180deg, #F0F7FF, #F8FBFF)" }}>
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-14">
            <SectionLabel>Live Analytics</SectionLabel>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(31px, 4vw, 43px)", fontWeight: 600, color: "#102A4A", letterSpacing: "0" }}>
              Multi-Parameter Monitoring
            </h2>
          </div>

          <div className="flex items-center gap-1 mb-6 justify-center">
            {["temperature", "humidity", "pressure"].map((tab) => (
              <button
                key={tab}
                onClick={() => setChartTab(tab)}
                className="px-4 py-2 rounded-full text-sm font-semibold capitalize transition-all duration-200"
                style={{
                  background: chartTab === tab ? "linear-gradient(135deg,#3269AB,#5792D7)" : "rgba(255,255,255,0.8)",
                  color: chartTab === tab ? "white" : "#1F4F82",
                  border: chartTab === tab ? "none" : "1px solid rgba(198,224,255,0.7)",
                  cursor: "pointer",
                  boxShadow: chartTab === tab ? "0 2px 12px rgba(50,105,171,0.3)" : "none",
                }}
              >
                {tab}
              </button>
            ))}
          </div>

          <div className="glass-card p-6">
            <div className="flex items-center justify-between mb-5">
              <div>
                <p style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 19, color: "#102A4A" }}>
                  {chartTab === "temperature" ? "Temperature (°C)" : chartTab === "humidity" ? "Relative Humidity (%)" : "Atmospheric Pressure (hPa)"}
                </p>
                <p className="text-xs mt-0.5" style={{ color: "#82B5F0" }}>
                  {backendStatus === "online" ? "Recent backend history" : "Last persisted backend history"}
                </p>
              </div>
              <div
                className="flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full"
                style={{
                  background: streamConnected ? "rgba(34,197,94,0.1)" : "rgba(245,158,11,0.12)",
                  color: streamConnected ? "#16a34a" : "#a16207",
                }}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${streamConnected ? "bg-green-500" : "bg-amber-500"}`} />
                {streamConnected ? "Live Feed" : "Last Known Data"}
              </div>
            </div>

            <ResponsiveContainer width="100%" height={220}>
              {chartTab === "temperature" ? (
                <LineChart data={temperatureChartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(169,206,255,0.3)" />
                  <XAxis dataKey="t" tick={{ fontSize: 13, fill: "#82B5F0" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 13, fill: "#82B5F0" }} axisLine={false} tickLine={false} domain={[27, 37]} />
                  <Tooltip contentStyle={{ background: "rgba(255,255,255,0.95)", border: "1px solid rgba(198,224,255,0.6)", borderRadius: 12, fontSize: 14 }} />
                  <ReferenceLine y={32} stroke="rgba(239,68,68,0.3)" strokeDasharray="4 2" label={{ value: "Warning", position: "right", fontSize: 12, fill: "#dc2626" }} />
                  <Line type="natural" dataKey="aws1" stroke="#dc2626" strokeWidth={2} dot={false} name="AWS-001" />
                  <Line type="natural" dataKey="aws2" stroke="#3269AB" strokeWidth={2} dot={false} name="AWS-002" />
                  <Line type="natural" dataKey="trusted" stroke="#16a34a" strokeWidth={2} dot={false} strokeDasharray="4 2" name="Trusted" />
                </LineChart>
              ) : (
                <AreaChart data={secondaryChartData}>
                  <defs>
                    <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#5792D7" stopOpacity={0.15} />
                      <stop offset="100%" stopColor="#5792D7" stopOpacity={0.01} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(169,206,255,0.3)" />
                  <XAxis dataKey="t" tick={{ fontSize: 13, fill: "#82B5F0" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 13, fill: "#82B5F0" }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: "rgba(255,255,255,0.95)", border: "1px solid rgba(198,224,255,0.6)", borderRadius: 12, fontSize: 14 }} />
                  <Area type="natural" dataKey={chartTab} stroke="#3269AB" strokeWidth={2} fill="url(#areaGrad)" dot={false} name={chartTab} />
                </AreaChart>
              )}
            </ResponsiveContainer>
          </div>

          <div className="text-center mt-8">
            <button
              onClick={onOpenDashboard}
              className="inline-flex items-center gap-2 px-5 py-2.5 font-semibold text-sm transition-all duration-200"
              style={{
                background: "rgba(50,105,171,0.08)",
                border: "1px solid rgba(50,105,171,0.2)",
                borderRadius: 99,
                color: "#3269AB",
                cursor: "pointer",
              }}
            >
              Explore Live Analytics
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 7h10M8 3l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>
        </div>
      </section>

      {/* ── HARDWARE + SOFTWARE STACK ──────────────────────────────── */}
      <section id="technology" className="py-24 px-6" style={{ background: "#F8FBFF" }}>
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-14">
            <SectionLabel>Technology</SectionLabel>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(31px, 4vw, 43px)", fontWeight: 600, color: "#102A4A", letterSpacing: "0" }}>
              Hardware & Software Stack
            </h2>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            {(["hardware", "software"] as const).map((type) => (
              <div key={type} className="glass-card p-7">
                <div className="flex items-center gap-3 mb-6">
                  <div className="w-10 h-10 rounded-2xl flex items-center justify-center text-xl" style={{ background: "rgba(50,105,171,0.08)" }}>
                    {type === "hardware" ? "🔲" : "⚡"}
                  </div>
                  <h3 style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 20, color: "#102A4A" }}>
                    {type === "hardware" ? "Hardware" : "Software"}
                  </h3>
                </div>
                <div className="space-y-3">
                  {stackItems[type].map((item) => (
                    <div key={item.label} className="flex items-center gap-3 py-2.5 px-3 rounded-xl transition-all duration-150" style={{ border: "1px solid rgba(198,224,255,0.4)" }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "rgba(50,105,171,0.05)"; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = ""; }}
                    >
                      <span className="text-lg">{item.icon}</span>
                      <div>
                        <p className="text-sm font-semibold" style={{ color: "#102A4A" }}>{item.label}</p>
                        <p className="text-xs" style={{ color: "#82B5F0" }}>{item.sub}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── HARDWARE FAULT TESTING ─────────────────────────────────── */}
      <section id="hardware-testing" className="py-24 px-6" style={{ background: "linear-gradient(180deg, #F0F7FF, #F8FBFF)" }}>
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-14">
            <SectionLabel>Hardware Fault Testing</SectionLabel>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(31px, 4vw, 43px)", fontWeight: 600, color: "#102A4A", letterSpacing: "0" }}>
              Inject controlled faults into live telemetry<br />and let SkyGuard detect them.
            </h2>
          </div>

          <div className="glass-card p-8">
            <div className="grid md:grid-cols-2 gap-8">
              {/* Controls */}
              <div>
                <p className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "#82B5F0" }}>AWS-001 Physical Buttons</p>
                <div className="space-y-4">
                  <div>
                    <p className="text-xs font-semibold mb-2" style={{ color: "#1F4F82" }}>Station</p>
                    <div className="flex gap-2">
                      {["AWS-001"].map((s) => (
                        <button key={s} className="px-3 py-1.5 rounded-lg text-sm font-semibold" style={{ background: s === "AWS-001" ? "linear-gradient(135deg,#3269AB,#5792D7)" : "rgba(255,255,255,0.8)", color: s === "AWS-001" ? "white" : "#1F4F82", border: "1px solid rgba(198,224,255,0.6)", cursor: "pointer" }}>
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <p className="text-xs font-semibold mb-2" style={{ color: "#1F4F82" }}>Fault test</p>
                    <div className="flex flex-wrap gap-2">
                      {["Spike", "Freeze", "Drift", "Data Loss", "Corruption"].map((s) => (
                        <button
                          key={s}
                          onClick={() => setFaultScenario(s)}
                          className="px-3 py-1.5 rounded-full text-xs font-semibold transition-all duration-150"
                          style={{
                            background: faultScenario === s ? "rgba(50,105,171,0.12)" : "rgba(255,255,255,0.7)",
                            color: faultScenario === s ? "#3269AB" : "#1F4F82",
                            border: `1px solid ${faultScenario === s ? "rgba(50,105,171,0.3)" : "rgba(198,224,255,0.5)"}`,
                            cursor: "pointer",
                          }}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <p className="text-xs font-semibold mb-2" style={{ color: "#1F4F82" }}>Injected channel</p>
                    <div className="px-3 py-2 rounded-lg text-xs font-semibold" style={{ background: "linear-gradient(135deg,#3269AB,#5792D7)", color: "white", border: "1px solid rgba(50,105,171,0.35)" }}>
                      DS18B20 primary temperature only · DHT22/BMP280 stay live
                    </div>
                  </div>

                  <div className="flex gap-3 pt-2">
                    <button
                      onClick={onOpenDashboard}
                      className="flex-1 py-2.5 rounded-xl text-sm font-bold text-white"
                      style={{ background: "linear-gradient(135deg,#3269AB,#5792D7)", border: "none", cursor: "pointer" }}
                    >
                      Open Live Dashboard
                    </button>
                    <button onClick={onOpenDashboard} className="flex-1 py-2.5 rounded-xl text-sm font-semibold" style={{ background: "rgba(255,255,255,0.8)", border: "1px solid rgba(198,224,255,0.6)", color: "#1F4F82", cursor: "pointer" }}>
                      Open Dashboard
                    </button>
                  </div>
                </div>
              </div>

              {/* Live result */}
              <div className="flex flex-col justify-center">
                <div className="rounded-2xl p-6" style={{ background: "linear-gradient(135deg, rgba(50,105,171,0.05), rgba(130,181,240,0.08))", border: "1px solid rgba(198,224,255,0.5)" }}>
                  <p className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "#82B5F0" }}>Illustrative Preview</p>
                  <div className="flex items-center justify-center gap-4 mb-5">
                    <div className="text-center">
                      <p className="text-xs font-medium mb-1" style={{ color: "#82B5F0" }}>Input</p>
                      <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 30, color: "#1F4F82" }}>29.2°C</p>
                    </div>
                    <div className="flex flex-col items-center gap-1">
                      <svg width="40" height="16" viewBox="0 0 40 16" fill="none">
                        <path d="M2 8h32M28 2l8 6-8 6" stroke="#dc2626" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                      <p className="text-xs font-semibold" style={{ color: "#dc2626" }}>{faultScenario}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs font-medium mb-1" style={{ color: "#82B5F0" }}>Output</p>
                      <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 30, color: "#dc2626" }}>
                        {faultScenario === "Spike" ? "+6°C step" : faultScenario === "Freeze" ? "held value" : faultScenario === "Drift" ? "+2°C ramp" : faultScenario === "Data Loss" ? "DS omitted" : "-127°C"}
                      </p>
                    </div>
                  </div>

                  <div className="rounded-xl px-4 py-3" style={{ background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.15)" }}>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-xs font-bold" style={{ color: "#dc2626" }}>
                          {`${faultScenario} test pattern`}
                        </p>
                        <p className="text-xs mt-0.5" style={{ color: "#1F4F82" }}>AWS-001 · Temperature</p>
                      </div>
                      <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 15, color: "#3269AB" }}>Preview</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── IMPACT ─────────────────────────────────────────────────── */}
      <section id="impact" className="py-24 px-6" style={{ background: "#F8FBFF" }}>
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-14">
            <SectionLabel>Impact</SectionLabel>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(31px, 4vw, 43px)", fontWeight: 600, color: "#102A4A", letterSpacing: "0" }}>
              More reliable environmental data<br />means better decisions.
            </h2>
          </div>

          <div className="grid md:grid-cols-3 gap-5">
            {impacts.map((item, i) => (
              <div
                key={item.title}
                className="glass-card p-6 transition-all duration-200"
                style={{ gridColumn: i >= 3 ? "span 1" : "span 1" }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.transform = "translateY(-3px)"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.transform = ""; }}
              >
                <div className="text-3xl mb-4">{item.icon}</div>
                <h3 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 18, color: "#102A4A", marginBottom: 8 }}>
                  {item.title}
                </h3>
                <p style={{ fontSize: 15, color: "#1F4F82", lineHeight: 1.6 }}>{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── FINAL CTA ──────────────────────────────────────────────── */}
      <section className="py-24 px-6" style={{ background: "linear-gradient(180deg, #F0F7FF, #E8F3FF)" }}>
        <div className="max-w-2xl mx-auto">
          <div
            className="glass-card p-12 text-center relative overflow-hidden"
            style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.9), rgba(232,243,255,0.8))" }}
          >
            <div className="absolute top-0 left-0 right-0 h-1" style={{ background: "linear-gradient(90deg, #3269AB, #5792D7, #82B5F0)" }} />
            <div style={{ fontFamily: "var(--font-display)", fontSize: "clamp(29px, 3vw, 39px)", fontWeight: 700, color: "#102A4A", lineHeight: 1.15, marginBottom: 16 }}>
              See SkyGuard AI operating<br />in real time.
            </div>
            <p className="mb-8 text-base" style={{ color: "#1F4F82" }}>
              The live dashboard connects directly to your AWS nodes via WebSocket.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <button
                onClick={onOpenDashboard}
                className="flex items-center gap-2 px-8 py-3.5 font-bold text-white"
                style={{ background: "linear-gradient(135deg,#3269AB,#5792D7)", borderRadius: 14, border: "none", cursor: "pointer", fontSize: 17, boxShadow: "0 4px 24px rgba(50,105,171,0.4)" }}
              >
                Open Live Dashboard
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M2 7h10M8 3l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
              <button
                onClick={onOpenDashboard}
                className="px-8 py-3.5 font-semibold"
                style={{ background: "rgba(255,255,255,0.8)", border: "1px solid rgba(198,224,255,0.8)", borderRadius: 14, color: "#1F4F82", cursor: "pointer", fontSize: 17 }}
              >
                View Hardware Testing
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ── FOOTER ─────────────────────────────────────────────────── */}
      <footer className="py-12 px-6" style={{ background: "#F8FBFF", borderTop: "1px solid rgba(198,224,255,0.5)" }}>
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 flex items-center justify-center text-white text-xs font-bold" style={{ background: "linear-gradient(135deg,#3269AB,#5792D7)", borderRadius: 8 }}>S</div>
            <div>
              <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 16, color: "#102A4A" }}>SkyGuard AI</p>
              <p className="text-xs" style={{ color: "#82B5F0" }}>Smart India Hackathon Project</p>
            </div>
          </div>

          <nav className="flex flex-wrap items-center gap-6">
            {[["Overview", "#overview"], ["Technology", "#technology"], ["Hardware Testing", "#hardware-testing"], ["Impact", "#impact"]].map(([label, href]) => (
              <a key={label} href={href} className="text-sm font-medium transition-all duration-150" style={{ color: "#1F4F82", textDecoration: "none" }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "#3269AB"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "#1F4F82"; }}
              >
                {label}
              </a>
            ))}
            <button onClick={onOpenDashboard} className="text-sm font-semibold transition-all duration-150" style={{ background: "none", border: "none", color: "#3269AB", cursor: "pointer" }}>
              Dashboard →
            </button>
          </nav>

          <p className="text-xs" style={{ color: "#A9CEFF" }}>© 2026 SkyGuard AI</p>
        </div>
      </footer>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-center mb-3">
      <span className="px-3 py-1 text-xs font-bold uppercase tracking-widest rounded-full" style={{ background: "rgba(50,105,171,0.08)", color: "#3269AB", border: "1px solid rgba(50,105,171,0.15)" }}>
        {children}
      </span>
    </div>
  );
}

interface FloatingStatCardProps {
  label: string;
  value: string;
  sub: string;
  status: "healthy" | "warning" | "online" | "idle";
  delay: number;
}

function FloatingStatCard({ label, value, sub, status, delay }: FloatingStatCardProps) {
  const statusColor = { healthy: "#16a34a", warning: "#d97706", online: "#3269AB", idle: "#3269AB" }[status];
  const statusBg = { healthy: "rgba(22,163,74,0.1)", warning: "rgba(217,119,6,0.1)", online: "rgba(50,105,171,0.1)", idle: "rgba(50,105,171,0.08)" }[status];
  const statusLabel = { healthy: "Healthy", warning: "Warning", online: "Online", idle: "Awaiting data" }[status];

  return (
    <div
      className="glass-card px-4 py-3.5 w-52"
      style={{ animationDelay: `${delay}s` }}
    >
      <p className="text-xs font-medium mb-1.5" style={{ color: "#82B5F0" }}>{label}</p>
      <p style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 24, color: "#102A4A" }}>{value}</p>
      <p className="text-xs mt-1" style={{ color: "#5792D7" }}>{sub}</p>
      <div className="mt-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold" style={{ background: statusBg, color: statusColor }}>
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: statusColor }} />
        {statusLabel}
      </div>
    </div>
  );
}
