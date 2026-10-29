import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { LockOutlined } from "@ant-design/icons";
import { resetPassword } from "../api/governance";

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const initialToken = useMemo(() => params.get("token") || "", [params]);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<{ token: string; newPassword: string; confirm: string }>();

  const onFinish = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      await resetPassword(values.token.trim(), values.newPassword);
      message.success("密码已重置，请使用新密码登录");
      navigate("/login", { replace: true });
    } catch (err) {
      message.error(err instanceof Error ? err.message : "重置失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-linear-to-br from-login-gradient-from to-login-gradient-to">
      <div className="w-full max-w-auth p-6">
        <div className="mb-8 text-center">
          <Typography.Title level={2} className="mb-1!">
            重置密码
          </Typography.Title>
        </div>
        <Card bordered={false} className="rounded-xl shadow-md">
          <Form
            form={form}
            layout="vertical"
            initialValues={{ token: initialToken }}
            onFinish={() => void onFinish()}
          >
            <Form.Item name="token" label="重置令牌" rules={[{ required: true, message: "请输入令牌" }]}>
              <Input.TextArea rows={3} />
            </Form.Item>
            <Form.Item
              name="newPassword"
              label="新密码"
              rules={[
                { required: true, message: "请输入新密码" },
                { min: 6, message: "至少 6 位" },
              ]}
            >
              <Input.Password prefix={<LockOutlined />} size="large" autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              name="confirm"
              label="确认密码"
              dependencies={["newPassword"]}
              rules={[
                { required: true, message: "请确认密码" },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue("newPassword") === value) return Promise.resolve();
                    return Promise.reject(new Error("两次输入不一致"));
                  },
                }),
              ]}
            >
              <Input.Password prefix={<LockOutlined />} size="large" autoComplete="new-password" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={submitting} block size="large">
                确认重置
              </Button>
            </Form.Item>
          </Form>
          <div className="text-center">
            <Link to="/login">返回登录</Link>
          </div>
        </Card>
      </div>
    </div>
  );
}
