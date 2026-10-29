import { useCallback, useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { Button, Card, Form, Input, Result, Typography, message } from "antd";
import { LockOutlined, MailOutlined, ReloadOutlined, SafetyCertificateOutlined, UserOutlined } from "@ant-design/icons";
import { getApiBaseUrl } from "../api/http";
import { BrandLogo } from "../components/BrandLogo";
import { useAuth } from "../context/AuthContext";

/**
 * Registration page — email + username + password.
 * On success the account stays pending admin activation (no auto-login).
 */
export default function RegisterPage() {
  const { authenticated, register } = useAuth();
  const [submitting, setSubmitting] = useState(false);
  const [pendingEmail, setPendingEmail] = useState<string | null>(null);
  const [captcha, setCaptcha] = useState<{ id: string; image: string } | null>(null);
  const [form] = Form.useForm<{
    email: string;
    username: string;
    password: string;
    confirmPassword: string;
    captchaCode?: string;
  }>();

  const loadCaptcha = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/auth/captcha`);
      if (!res.ok) return;
      const body = (await res.json()) as {
        enabled?: boolean;
        captchaId?: string | null;
        image?: string | null;
      };
      if (body.enabled && body.captchaId && body.image) {
        setCaptcha({ id: body.captchaId, image: body.image });
        form.setFieldValue("captchaCode", undefined);
      } else {
        setCaptcha(null);
      }
    } catch {
      // Backend still enforces captcha when enabled.
    }
  }, [form]);

  useEffect(() => {
    void loadCaptcha();
  }, [loadCaptcha]);

  if (authenticated) {
    return <Navigate to="/workbench" replace />;
  }

  const handleRegister = async () => {
    let values: {
      email: string;
      username: string;
      password: string;
      confirmPassword: string;
      captchaCode?: string;
    };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    if (values.password !== values.confirmPassword) {
      message.error("两次输入的密码不一致");
      return;
    }

    if (captcha && !values.captchaCode?.trim()) {
      message.error("请填写验证码");
      return;
    }

    setSubmitting(true);
    try {
      const result = await register(values.email.trim(), values.username.trim(), values.password, {
        captchaId: captcha?.id,
        captchaCode: values.captchaCode?.trim(),
      });
      setPendingEmail(result.email);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "注册失败";
      message.error(msg);
      void loadCaptcha();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-linear-to-br from-login-gradient-from to-login-gradient-to">
      <div className="w-full max-w-auth p-6">
        <div className="mb-8 text-center">
          <BrandLogo className="mx-auto mb-1 h-10 w-auto" />
          <Typography.Paragraph type="secondary" className="text-base!">
            通用智能科研平台 · 创建新账户
          </Typography.Paragraph>
        </div>

        <Card bordered={false} className="rounded-xl shadow-md">
          {pendingEmail ? (
            <Result
              status="success"
              title="注册成功，等待管理员激活"
              subTitle={`账号 ${pendingEmail} 已创建，当前为未激活状态。管理员激活并分配额度后，即可登录使用。`}
              extra={
                <Link to="/login">
                  <Button type="primary">返回登录</Button>
                </Link>
              }
            />
          ) : (
            <>
              <Form
                form={form}
                layout="vertical"
                onFinish={() => void handleRegister()}
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
                  name="username"
                  label="用户名"
                  rules={[
                    { required: true, message: "请输入用户名" },
                    { min: 2, message: "用户名至少 2 个字符" },
                    { max: 50, message: "用户名不超过 50 个字符" },
                    {
                      pattern: /^[a-zA-Z0-9_一-鿿]+$/,
                      message: "用户名只能包含字母、数字、下划线或中文"
                    }
                  ]}
                >
                  <Input
                    prefix={<UserOutlined />}
                    placeholder="请输入用户名"
                    size="large"
                    autoComplete="username"
                  />
                </Form.Item>

                <Form.Item
                  name="password"
                  label="密码"
                  rules={[
                    { required: true, message: "请输入密码" },
                    { min: 6, message: "密码至少 6 个字符" }
                  ]}
                >
                  <Input.Password
                    prefix={<LockOutlined />}
                    placeholder="至少 6 个字符"
                    size="large"
                    autoComplete="new-password"
                  />
                </Form.Item>

                <Form.Item
                  name="confirmPassword"
                  label="确认密码"
                  dependencies={["password"]}
                  rules={[
                    { required: true, message: "请确认密码" },
                    ({ getFieldValue }) => ({
                      validator(_, value) {
                        if (!value || getFieldValue("password") === value) {
                          return Promise.resolve();
                        }
                        return Promise.reject(new Error("两次输入的密码不一致"));
                      }
                    })
                  ]}
                >
                  <Input.Password
                    prefix={<LockOutlined />}
                    placeholder="再次输入密码"
                    size="large"
                    autoComplete="new-password"
                  />
                </Form.Item>

                {captcha ? (
                  <Form.Item label="验证码" required>
                    <div className="flex items-center gap-2">
                      <Form.Item
                        name="captchaCode"
                        noStyle
                        rules={[{ required: true, message: "请填写验证码" }]}
                      >
                        <Input
                          prefix={<SafetyCertificateOutlined />}
                          placeholder="请输入图中字符"
                          size="large"
                          autoComplete="off"
                          maxLength={8}
                        />
                      </Form.Item>
                      <button
                        type="button"
                        className="shrink-0 rounded border border-neutral-200 bg-white p-0"
                        onClick={() => void loadCaptcha()}
                        title="点击刷新验证码"
                      >
                        <img src={captcha.image} alt="验证码" className="block h-10 w-40" />
                      </button>
                      <Button
                        type="text"
                        icon={<ReloadOutlined />}
                        onClick={() => void loadCaptcha()}
                        aria-label="刷新验证码"
                      />
                    </div>
                  </Form.Item>
                ) : null}

                <Form.Item>
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={submitting}
                    block
                    size="large"
                  >
                    注册
                  </Button>
                </Form.Item>
              </Form>

              <div className="text-center">
                <Typography.Text type="secondary">
                  已有账号？
                  <Link to="/login">去登录</Link>
                </Typography.Text>
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
