import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Card, Form, Input, Typography, message } from "antd";
import { MailOutlined } from "@ant-design/icons";
import { forgotPassword } from "../api/governance";

export default function ForgotPasswordPage() {
  const [submitting, setSubmitting] = useState(false);
  const [devToken, setDevToken] = useState<string | null>(null);
  const [form] = Form.useForm<{ email: string }>();

  const onFinish = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      const res = await forgotPassword(values.email.trim());
      message.success(res.message || "若邮箱已注册，重置令牌已签发");
      setDevToken(res.resetToken ?? null);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "请求失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-linear-to-br from-login-gradient-from to-login-gradient-to">
      <div className="w-full max-w-auth p-6">
        <div className="mb-8 text-center">
          <Typography.Title level={2} className="mb-1!">
            找回密码
          </Typography.Title>
          <Typography.Paragraph type="secondary">输入注册邮箱以获取重置令牌</Typography.Paragraph>
        </div>
        <Card bordered={false} className="rounded-xl shadow-md">
          <Form form={form} layout="vertical" onFinish={() => void onFinish()}>
            <Form.Item
              name="email"
              label="邮箱"
              rules={[
                { required: true, message: "请输入邮箱" },
                { type: "email", message: "请输入有效邮箱" },
              ]}
            >
              <Input prefix={<MailOutlined />} size="large" autoComplete="email" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={submitting} block size="large">
                发送重置令牌
              </Button>
            </Form.Item>
          </Form>
          {devToken ? (
            <Alert
              className="mb-3"
              type="info"
              showIcon
              message="开发模式：重置令牌（生产环境将通过邮件发送）"
              description={
                <div>
                  <Typography.Paragraph copyable className="mb-2! break-all">
                    {devToken}
                  </Typography.Paragraph>
                  <Link to={`/reset-password?token=${encodeURIComponent(devToken)}`}>前往重置密码</Link>
                </div>
              }
            />
          ) : null}
          <div className="text-center">
            <Link to="/login">返回登录</Link>
          </div>
        </Card>
      </div>
    </div>
  );
}
