import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Spin, Typography } from "antd";
import { useAuth } from "../context/AuthContext";

/**
 * Route guard: redirect to /login if not authenticated.
 * Show spinner while initial auth check is in progress.
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { initialized, authenticated } = useAuth();
  const location = useLocation();

  if (!initialized) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4">
        <Spin size="large" />
        <Typography.Text type="secondary">验证登录状态…</Typography.Text>
      </div>
    );
  }

  if (!authenticated) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  return <>{children}</>;
}
