import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  HeartPulse,
  Home,
  LayoutDashboard,
  RadioTower,
  MapPinned,
  ShieldCheck,
  LogOut,
  Users,
  Camera,
  Wrench,
} from "lucide-react";
import type { AnomalyEvent, NodeId } from "../api/types";
import {
  AnalyticsView,
  AnomaliesView,
  FieldIntelligenceView,
  LiveSensorsView,
  OverviewView,
  SystemHealthView,
  TrustedDataView,
} from "../components/dashboard/DashboardViews";
import { AnomalyDrawer } from "../components/dashboard/EventsPanels";
import { DashboardSkeleton, ModeBanner, StatusPill } from "../components/dashboard/StatusUi";
import { useSkyGuard } from "../hooks/useSkyGuard";
import { formatRelativeTime } from "../utils/format";
import CommunityIntelligence from "../components/dashboard/CommunityIntelligence";
import VisionIntelligence from "../components/dashboard/VisionIntelligence";
import SensorMaintenance from "../components/dashboard/SensorMaintenance";

type DashboardView = "overview" | "sensors" | "analytics" | "anomalies" | "field_intelligence" | "community" | "vision" | "maintenance" | "trusted" | "health";

const navItems: Array<{ id: DashboardView; label: string; icon: typeof Activity }> = [
  { id: "overview", icon: LayoutDashboard, label: "Overview" },
  { id: "sensors", icon: RadioTower, label: "Live Sensors" },
  { id: "analytics", icon: BarChart3, label: "Analytics" },
  { id: "anomalies", icon: AlertTriangle, label: "Anomalies" },
  { id: "field_intelligence", icon: MapPinned, label: "Field Intelligence" },
  { id: "community", icon: Users, label: "Community Intelligence" },
  { id: "vision", icon: Camera, label: "Vision Intelligence" },
  { id: "maintenance", icon: Wrench, label: "Sensor Maintenance" },
  { id: "trusted", icon: ShieldCheck, label: "Trusted Data" },
  { id: "health", icon: HeartPulse, label: "System Health" },
];

interface DashboardProps {
  onBack: () => void;
  onLogout: () => void;
}

export default function Dashboard({ onBack, onLogout }: DashboardProps) {
  const {
    backendStatus,
    error,
    fieldNotice,
    clearFieldNotice,
    lastUpdatedAt,
    loading,
    mqttStatus,
    socketStatus,
    summary,
  } = useSkyGuard();
  const [activeNav, setActiveNav] = useState<DashboardView>("overview");
  const [selectedNode, setSelectedNode] = useState<NodeId>("AWS_001");
  const [drawerEvent, setDrawerEvent] = useState<AnomalyEvent | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: "success" | "error" } | null>(null);
  const [, setClock] = useState(0);
  const activeAnomalyCount = summary.nodes.reduce((count, node) => count + node.active_anomalies.length, 0);

  useEffect(() => {
    const interval = window.setInterval(() => setClock((value) => value + 1), 1_000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(null), 3_500);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    if (!fieldNotice) return;
    setToast({ message: `${fieldNotice.title}: ${fieldNotice.message}`, tone: "success" });
    clearFieldNotice();
  }, [clearFieldNotice, fieldNotice]);

  const notify = useCallback(
    (message: string, tone: "success" | "error" = "success") => setToast({ message, tone }),
    [],
  );
  const closeDrawer = useCallback(() => setDrawerEvent(null), []);

  const activeView = useMemo(() => {
    const common = { selectedNode, onSelectNode: setSelectedNode, onSelectEvent: setDrawerEvent };
    switch (activeNav) {
      case "sensors":
        return <LiveSensorsView {...common} />;
      case "analytics":
        return <AnalyticsView {...common} />;
      case "anomalies":
        return <AnomaliesView {...common} />;
      case "trusted":
        return <TrustedDataView {...common} />;
      case "field_intelligence":
        return <FieldIntelligenceView {...common} onNotify={notify} />;
      case "community":
        return <CommunityIntelligence onNotify={notify} />;
      case "vision":
        return <VisionIntelligence onNotify={notify} />;
      case "maintenance":
        return <SensorMaintenance />;
      case "health":
        return <SystemHealthView />;
      default:
        return <OverviewView {...common} />;
    }
  }, [activeNav, notify, selectedNode]);

  return (
    <div className="dashboard-shell">
      <aside className="dashboard-sidebar" aria-label="Dashboard navigation">
        <div className="dashboard-brand">
          <span>S</span>
          <div><strong>SkyGuard AI</strong><small>Live Monitoring</small></div>
        </div>

        <nav className="dashboard-nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button key={item.id} className={activeNav === item.id ? "active" : ""} onClick={() => setActiveNav(item.id)} aria-current={activeNav === item.id ? "page" : undefined} title={item.label}>
                <Icon size={17} aria-hidden="true" />
                <span>{item.label}</span>
                {item.id === "anomalies" && activeAnomalyCount > 0 && <em>{activeAnomalyCount}</em>}
              </button>
            );
          })}
        </nav>
        <button className="dashboard-logout" onClick={onLogout}><LogOut size={16} /><span>Logout</span></button>

        <div className="sidebar-footer">
          <button onClick={onBack}><Home size={16} /><span>Homepage</span></button>
          <div className="sidebar-runtime">
            <span className={backendStatus === "online" ? "online" : "offline"} />
            <div><strong>Backend {backendStatus}</strong><small>{summary.system.detector_mode}</small></div>
          </div>
        </div>
      </aside>

      <div className="dashboard-main">
        <header className="dashboard-header">
          <div className="dashboard-title">
            <p className="panel-eyebrow">SkyGuard AI · Live Monitoring Platform</p>
            <h1>{navItems.find((item) => item.id === activeNav)?.label}</h1>
          </div>
          <div className="dashboard-statuses">
            <StatusPill label="Backend" state={backendStatus} pulse={backendStatus === "online"} />
            <StatusPill label="MQTT" state={mqttStatus ? (mqttStatus.connected ? "connected" : "disconnected") : "unavailable"} pulse={mqttStatus?.connected} />
            <StatusPill label="WebSocket" state={socketStatus} pulse={socketStatus === "connected"} />
            <span className="last-update-chip"><small>Last update</small><strong>{formatRelativeTime(lastUpdatedAt)}</strong></span>
          </div>
        </header>

        <ModeBanner online={backendStatus === "online" && !error} message={error} />
        <main id="dashboard-main" className="dashboard-content">
          {loading && backendStatus === "checking" ? <DashboardSkeleton /> : activeView}
        </main>
      </div>

      <AnomalyDrawer event={drawerEvent} onClose={closeDrawer} />
      {toast && <div className={`dashboard-toast ${toast.tone}`} role="status">{toast.tone === "success" ? "✓" : "!"}<span>{toast.message}</span></div>}
    </div>
  );
}
