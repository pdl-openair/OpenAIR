import { useEffect, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Card, Spin, Typography } from "antd";
import { useAuth } from "../context/AuthContext";

/**
 * Lands after OAuth callback redirect with ?token=...
 */
export default function OAuthCallbackPage() {
  const { authenticated, loginWithToken } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = searchParams.get("token")?.trim();
    if (!token) {
      setError("未收到登录凭证，请返回登录页重试。");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        await loginWithToken(token);
        if (!cancelled) {
          navigate("/workbench", { replace: true });
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "OAuth 登录失败");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loginWithToken, navigate, searchParams]);

  if (authenticated && !error) {
    return <Navigate to="/workbench" replace />;
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-linear-to-br from-login-gradient-from to-login-gradient-to">
      <Card className="w-full max-w-auth rounded-xl shadow-md" bordered={false}>
        {error ? (
          <Alert
            type="error"
            showIcon
            title="第三方登录未完成"
            description={
              <>
                {error}{" "}
                <Typography.Link href="/login">返回登录</Typography.Link>
              </>
            }
          />
        ) : (
          <div className="flex flex-col items-center gap-3 py-6">
            <Spin size="large" />
            <Typography.Text type="secondary">正在完成第三方登录…</Typography.Text>
          </div>
        )}
      </Card>
    </div>
  );
}
