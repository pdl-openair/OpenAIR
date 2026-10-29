import { useEffect, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { App, Spin, Typography } from "antd";
import { useAuth } from "../context/AuthContext";

/**
 * Route guard: redirect non-admin users away from admin pages (S03-14-05).
 * Waits for auth initialization, then checks role=admin.
 */
export function AdminGuard({ children }: { children: ReactNode }) {
  const { initialized, authenticated, isAdmin } = useAuth();
  const location = useLocation();
  const { message } = App.useApp();

  useEffect(() => {
    if (initialized && authenticated && !isAdmin) {
      message.warning("需要管理员权限");
    }
  }, [initialized, authenticated, isAdmin, message]);

  if (!initialized) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4">
        <Spin size="large" />
        <Typography.Text type="secondary">验证权限…</Typography.Text>
      </div>
    );
  }

  if (!authenticated) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  if (!isAdmin) {
    return <Navigate to="/workbench" replace />;
  }

  return <>{children}</>;
}
