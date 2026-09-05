import { CloudSun, Menu, ShieldCheck, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";

export default function PublicShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <div className="public-shell"><header className="public-nav"><NavLink to="/" className="public-brand"><span><CloudSun size={19} /></span><strong>SkyGuard AI</strong></NavLink><button className="public-menu" onClick={() => setOpen(!open)} aria-label="Toggle navigation">{open ? <X /> : <Menu />}</button><nav className={open ? "open" : ""}><NavLink to="/public">Nearby Weather</NavLink><a href="/public#stations">Stations</a><NavLink to="/public/reports">Community Reports</NavLink><NavLink to="/public/report" className="report-nav">Report Condition</NavLink><NavLink to="/admin/login" className="admin-nav"><ShieldCheck size={15} />Admin Portal</NavLink></nav></header>{children}</div>;
}
