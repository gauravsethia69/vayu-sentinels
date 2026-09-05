import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { SkyGuardProvider } from "./context/SkyGuardContext";
import HomePage from "./pages/HomePage";
import Dashboard from "./pages/Dashboard";
import EntryPage from "./pages/EntryPage";
import PublicPortal from "./pages/PublicPortal";
import PublicReportPage from "./pages/PublicReportPage";
import PublicReportsPage from "./pages/PublicReportsPage";
import AdminLogin from "./pages/AdminLogin";
import { AuthProvider, ProtectedAdminRoute, useAuth } from "./auth/AuthContext";

function AppRoutes() {
  const navigate = useNavigate();
  const { logout } = useAuth();

  return (
    <Routes>
      <Route path="/" element={<EntryPage />} />
      <Route path="/about" element={<HomePage onOpenDashboard={() => navigate("/admin/login")} />} />
      <Route path="/public" element={<PublicPortal />} />
      <Route path="/public/report" element={<PublicReportPage />} />
      <Route path="/public/reports" element={<PublicReportsPage />} />
      <Route path="/admin/login" element={<AdminLogin />} />
      <Route path="/admin/dashboard" element={<ProtectedAdminRoute><Dashboard onBack={() => navigate("/")} onLogout={() => void logout().then(() => navigate("/"))} /></ProtectedAdminRoute>} />
      <Route path="/dashboard" element={<Navigate to="/admin/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider><SkyGuardProvider><AppRoutes /></SkyGuardProvider></AuthProvider>
    </BrowserRouter>
  );
}
