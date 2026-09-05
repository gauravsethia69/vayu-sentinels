import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api } from "../api/endpoints";
import { AUTH_STORAGE_KEY } from "../api/client";
import type { AdminSession } from "../api/types";

interface AuthValue { session: AdminSession | null; checking: boolean; login: (name: string, password: string) => Promise<void>; logout: () => Promise<void>; }
const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AdminSession | null>(() => {
    try { return JSON.parse(sessionStorage.getItem(AUTH_STORAGE_KEY) || "null"); } catch { return null; }
  });
  const [checking, setChecking] = useState(Boolean(session));
  useEffect(() => {
    if (!session) { setChecking(false); return; }
    void api.getAuthSession().catch(() => { sessionStorage.removeItem(AUTH_STORAGE_KEY); setSession(null); }).finally(() => setChecking(false));
  }, []);
  const value = useMemo<AuthValue>(() => ({
    session, checking,
    login: async (name, password) => { const next = await api.login(name, password); sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(next)); setSession(next); },
    logout: async () => { try { await api.logout(); } finally { sessionStorage.removeItem(AUTH_STORAGE_KEY); setSession(null); } },
  }), [checking, session]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error("useAuth must be used inside AuthProvider"); return value; }
export function ProtectedAdminRoute({ children }: { children: ReactNode }) {
  const { session, checking } = useAuth(); const location = useLocation();
  if (checking) return <div className="portal-loading" aria-label="Checking admin session"><span /></div>;
  return session ? children : <Navigate to="/admin/login" replace state={{ from: location.pathname }} />;
}
