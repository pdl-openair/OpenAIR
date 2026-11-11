import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import {
  getLlmSettings,
  testLlmSettings,
  updateLlmSettings,
  type GatewayProbeResult,
  type LlmSettingsView,
} from "../../api/adminLlmSettings";
import { userFacingErrorText } from "../../lib/userFacingLanguage";

/** 管理员模型与网关配置：保存即对规划/结构化执行热生效；测试连接可先测后存。
 * 同一份表单同时供 /admin/model-settings 与 /settings（管理员可见区块）使用。 */

interface FormValues {
  providerBaseUrl?: string;
  providerApiKey?: string;
  providerModel?: string;
  plannerModel?: string;
  anthropicBaseUrl?: string;
  anthropicApiKey?: string;
  anthropicAuthToken?: string;
  claudeModel?: string;
}

function ProbeResult({
  result,
  title,
}: {
  result: GatewayProbeResult | null;
  title: string;
}) {
  if (!result) return null;
  const tone = result.ok ? "success" : result.configured ? "error" : "warning";
  const description = result.ok
    ? `${result.status}${result.detail ? ` · ${result.detail}` : ""}${
        result.modelAvailable === false ? " · 注意：当前模型不在网关列表中" : ""
      }`
    : `${result.status}${result.detail ? ` · ${result.detail}` : ""}`;
  return (
    <Alert
      className="mb-3"
      type={tone}
      showIcon
      message={title}
      description={description}
    />
  );
}

export function LlmGatewaySettingsSection() {
  const [form] = Form.useForm<FormValues>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [view, setView] = useState<LlmSettingsView | null>(null);
  const [providerProbe, setProviderProbe] = useState<GatewayProbeResult | null>(
    null,
  );
  const [anthropicProbe, setAnthropicProbe] =
    useState<GatewayProbeResult | null>(null);

  useEffect(() => {
    let disposed = false;
    getLlmSettings()
      .then((settings) => {
        if (disposed) return;
        setView(settings);
        form.setFieldsValue({
          providerBaseUrl: settings.providerBaseUrl || "",
          providerModel: settings.providerModel || "",
          plannerModel: settings.plannerModel || "",
          anthropicBaseUrl: settings.anthropicBaseUrl || "",
          claudeModel: settings.claudeModel || "",
        });
      })
      .catch((error: unknown) => {
        if (disposed) return;
        message.error(
          userFacingErrorText(
            error instanceof Error ? error.message : String(error),
            "模型配置读取失败。",
          ),
        );
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [form]);

  const collectOverrides = (): FormValues => {
    const values = form.getFieldsValue();
    const overrides: FormValues = {};
    for (const [key, value] of Object.entries(values)) {
      if (typeof value === "string" && value.trim()) {
        (overrides as Record<string, string>)[key] = value.trim();
      }
    }
    return overrides;
  };

  const handleTest = async () => {
    if (testing) return;
    setTesting(true);
    setProviderProbe(null);
    setAnthropicProbe(null);
    try {
      const result = await testLlmSettings(collectOverrides());
      setProviderProbe(result.provider);
      setAnthropicProbe(result.anthropic);
    } catch (error) {
      message.error(
        userFacingErrorText(
          error instanceof Error ? error.message : String(error),
          "测试连接未完成。",
        ),
      );
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const result = await updateLlmSettings(collectOverrides());
      if (result.changed.length > 0) {
        message.success(`已保存并生效：${result.changed.join("、")}`);
      } else {
        message.info("没有需要保存的变更。");
      }
      setView(result.settings);
      form.setFieldValue("providerApiKey", "");
      form.setFieldValue("anthropicApiKey", "");
      form.setFieldValue("anthropicAuthToken", "");
    } catch (error) {
      message.error(
        userFacingErrorText(
          error instanceof Error ? error.message : String(error),
          "保存未完成，请检查填写内容。",
        ),
      );
    } finally {
      setSaving(false);
    }
  };

  const secretPlaceholder = (configured: boolean, preview: string) =>
    configured ? `已配置（${preview}），留空保持不变` : "未配置";

  return (
    <div className="w-full">
      <Alert
        type="info"
        showIcon
        message="模型与网关配置（仅管理员）"
        description="保存后立即对研究规划与结构化执行生效，无需重启 API；Skill 执行所在的 durable worker 需要重启后才会使用新配置。建议先用「测试连接」验证再保存。密钥仅保存在服务端，不会回显。"
      />
      <Form form={form} layout="vertical" disabled={loading}>
        <Row gutter={[16, 16]} className="mt-4">
          <Col xs={24} lg={12}>
            <Card title="规划器网关（OpenAI 兼容）" className="h-full">
              <Typography.Paragraph type="secondary">
                用于开题、研究方案与结构化执行。
              </Typography.Paragraph>
              <Form.Item
                name="providerBaseUrl"
                label="网关地址（Base URL）"
                rules={[{ type: "url", message: "请填写合法的 http(s) 地址" }]}
              >
                <Input placeholder="https://api.example.com（勿带 /v1/chat/completions）" />
              </Form.Item>
              <Form.Item name="providerApiKey" label="API Key">
                <Input.Password
                  autoComplete="new-password"
                  placeholder={secretPlaceholder(
                    !!view?.providerApiKey.configured,
                    view?.providerApiKey.preview ?? "",
                  )}
                />
              </Form.Item>
              <Form.Item name="providerModel" label="默认执行模型">
                <Input placeholder="如 deepseek-flash" />
              </Form.Item>
              <Form.Item
                name="plannerModel"
                label="规划器模型（开题/出方案）"
                extra="独立于默认执行模型；只改其一不会带动另一个。"
              >
                <Input placeholder="如 deepseek-flash" />
              </Form.Item>
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title="Skill 执行网关（Anthropic 兼容）" className="h-full">
              <Typography.Paragraph type="secondary">
                用于 Claude Agent SDK 执行科研 Skill。
              </Typography.Paragraph>
              <Form.Item
                name="anthropicBaseUrl"
                label="网关地址（Base URL）"
                rules={[{ type: "url", message: "请填写合法的 http(s) 地址" }]}
              >
                <Input placeholder="https://api.example.com" />
              </Form.Item>
              <Form.Item name="anthropicApiKey" label="API Key（x-api-key）">
                <Input.Password
                  autoComplete="new-password"
                  placeholder={secretPlaceholder(
                    !!view?.anthropicApiKey.configured,
                    view?.anthropicApiKey.preview ?? "",
                  )}
                />
              </Form.Item>
              <Form.Item
                name="anthropicAuthToken"
                label="Auth Token（Bearer，少数网关使用）"
              >
                <Input.Password
                  autoComplete="new-password"
                  placeholder={secretPlaceholder(
                    !!view?.anthropicAuthToken.configured,
                    view?.anthropicAuthToken.preview ?? "",
                  )}
                />
              </Form.Item>
              <Form.Item name="claudeModel" label="Skill 执行模型">
                <Input placeholder="如 deepseek-flash" />
              </Form.Item>
            </Card>
          </Col>
        </Row>
        <Space wrap className="mt-2">
          <Button
            onClick={() => void handleTest()}
            loading={testing}
            data-testid="llm-settings-test"
          >
            测试连接
          </Button>
          <Button
            type="primary"
            onClick={() => void handleSave()}
            loading={saving}
            data-testid="llm-settings-save"
          >
            保存并生效
          </Button>
          {view?.workerRestartRequired ? (
            <Tag color="warning">Skill worker 重启后使用新网关配置</Tag>
          ) : null}
        </Space>
      </Form>
      <div>
        <ProbeResult result={providerProbe} title="规划器网关" />
        <ProbeResult result={anthropicProbe} title="Skill 执行网关" />
      </div>
    </div>
  );
}
