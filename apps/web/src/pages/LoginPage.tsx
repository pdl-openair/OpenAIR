import { useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Button, Card, Divider, Form, Input, Space, Typography, message } from "antd";
import { LockOutlined, MailOutlined } from "@ant-design/icons";
import { getApiBaseUrl } from "../api/http";
import { BrandLogo } from "../components/BrandLogo";
import { useAuth } from "../context/AuthContext";

/** Fixed-size slot so GitLink / OpenI icons share one vertical axis. */
function OAuthIconSlot({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center leading-none [&>.anticon]:!m-0 [&>img]:block [&>img]:h-4 [&>img]:w-4 [&>img]:object-contain">
      {children}
    </span>
  );
}

/** OpenI brand mark sourced from https://www.openi.org.cn / openi.pcl.ac.cn. */
function OpenILogoIcon() {
  return <img src="/brand/openi-mark.svg" alt="" draggable={false} />;
}

/** GitLink brand mark: https://gitlink.org.cn/images/avatars/LaboratorySetting/1nav */
function GitLinkLogoIcon() {
  return <img src="/brand/gitlink-mark.png" alt="" draggable={false} />;
}

/**
 * Centered label row with a shared fixed width so:
 * - icon + text sit in the middle of each button
 * - icons stay on one vertical line across providers
 */
function OAuthLoginLabel({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <span className="mx-auto inline-flex w-[11.5rem] items-center justify-start gap-3">
      <OAuthIconSlot>{icon}</OAuthIconSlot>
      <span className="text-left">{text}</span>
    </span>
  );
}

const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  denied: "你取消了第三方授权。",
  invalid_state: "登录状态已失效，请重试。",
  exchange_failed: "第三方授权换票失败，请重试。",
  email_conflict:
    "该第三方邮箱已在本平台注册。请先用邮箱密码登录，后续再绑定（绑定功能即将提供）。",
  disabled: "该账号已被禁用，请联系管理员。",
  pending_activation: "账号已创建，请等待管理员激活后再登录。",
  not_configured: "该第三方登录未配置。",
};

/**
 * Login page — email + password authentication.
 * - Dev mode: quick-login with built-in test account.
 * - Link to /register for new accounts.
 * - Optional GitLink / OpenI OAuth when backend reports enabled.
 * - GitHub OAuth is temporarily paused (product decision); do not probe or render it.
 */
export default function LoginPage() {
  const { authenticated, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [submitting, setSubmitting] = useState(false);
  const [openiEnabled, setOpeniEnabled] = useState(false);
  const [gitlinkEnabled, setGitlinkEnabled] = useState(false);
  const [form] = Form.useForm<{ email: string; password: string }>();
  const oauthError = searchParams.get("oauth_error");
  const oauthProvidersEnabled = openiEnabled || gitlinkEnabled;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const probe = async (path: string): Promise<boolean> => {
        try {
          const res = await fetch(`${getApiBaseUrl()}${path}`);
          if (!res.ok) return false;
          const body = (await res.json()) as { enabled?: boolean };
          return Boolean(body.enabled);
        } catch {
          return false;
        }
      };
      // GitHub status is intentionally not probed while product support is paused.
      const [openi, gitlink] = await Promise.all([
        probe("/api/auth/oauth/openi/status"),
        probe("/api/auth/oauth/gitlink/status"),
      ]);
      if (!cancelled) {
        setOpeniEnabled(openi);
        setGitlinkEnabled(gitlink);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (authenticated) {
    const from = (location.state as { from?: string })?.from || "/workbench";
    return <Navigate to={from} replace />;
  }

  const handleLogin = async () => {
    let values: { email: string; password: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    setSubmitting(true);
    try {
      await login(values.email.trim(), values.password);
      message.success("登录成功");
      const from = (location.state as { from?: string })?.from || "/workbench";
      navigate(from, { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "登录失败";
      message.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDevLogin = async () => {
    form.setFieldsValue({ email: "dev@openair.dev", password: "dev123456" });
    setSubmitting(true);
    try {
      await login("dev@openair.dev", "dev123456");
      message.success("登录成功");
      const from = (location.state as { from?: string })?.from || "/workbench";
      navigate(from, { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "登录失败";
      message.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleOpeniLogin = () => {
    window.location.assign(`${getApiBaseUrl()}/api/auth/oauth/openi/start`);
  };

  const handleGitlinkLogin = () => {
    window.location.assign(`${getApiBaseUrl()}/api/auth/oauth/gitlink/start`);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-linear-to-br from-login-gradient-from to-login-gradient-to">
      <div className="w-full max-w-auth p-6">
        <div className="mb-8 text-center">
          <BrandLogo className="mx-auto mb-1 h-10 w-auto" />
          <Typography.Paragraph type="secondary" className="text-base!">
            通用智能科研平台 · 登录你的账户
          </Typography.Paragraph>
        </div>

        <Card bordered={false} className="rounded-xl shadow-md">
          {oauthError ? (
            <Alert
              className="mb-4"
              type="warning"
              showIcon
              title="第三方登录未完成"
              description={OAUTH_ERROR_MESSAGES[oauthError] ?? "请重试或改用邮箱密码登录。"}
            />
          ) : null}

          {oauthProvidersEnabled ? (
            <>
              <Space direction="vertical" size="middle" className="mb-2 w-full">
                {gitlinkEnabled ? (
                  <Button size="large" block onClick={handleGitlinkLogin}>
                    <OAuthLoginLabel icon={<GitLinkLogoIcon />} text="使用 GitLink 登录" />
                  </Button>
                ) : null}
                {openiEnabled ? (
                  <Button size="large" block onClick={handleOpeniLogin}>
                    <OAuthLoginLabel icon={<OpenILogoIcon />} text="使用 OpenI 登录" />
                  </Button>
                ) : null}
              </Space>
              <Divider plain>或使用邮箱</Divider>
            </>
          ) : null}

          <Form
            form={form}
            layout="vertical"
            onFinish={() => void handleLogin()}
          >
            <Form.Item
              name="email"
              label="邮箱"
              rules={[
                { required: true, message: "请输入邮箱" },
                { type: "email", message: "请输入有效的邮箱地址" }
              ]}
            >
              <Input
                prefix={<MailOutlined />}
                placeholder="your@email.com"
                size="large"
                autoComplete="email"
              />
            </Form.Item>

            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: "请输入密码" }]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="请输入密码"
                size="large"
                autoComplete="current-password"
              />
            </Form.Item>

            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={submitting}
                block
                size="large"
              >
                登录
              </Button>
            </Form.Item>
          </Form>

          <div className="flex flex-col items-center gap-1 text-center">
            <Typography.Text type="secondary">
              没有账号？
              <Link to="/register">立即注册</Link>
            </Typography.Text>
            <Typography.Text type="secondary">
              <Link to="/forgot-password">忘记密码？</Link>
            </Typography.Text>
          </div>

          {import.meta.env.DEV && (
            <div className="mt-2 flex flex-col items-center gap-1 border-t border-border pt-3 text-center">
              <Typography.Text type="secondary" className="text-xs!">
                开发模式
              </Typography.Text>
              <Button
                type="link"
                size="small"
                onClick={() => void handleDevLogin()}
                loading={submitting}
              >
                使用开发账号快速登录
              </Button>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
