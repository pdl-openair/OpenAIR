import { LlmGatewaySettingsSection } from "../components/settings/LlmGatewaySettingsSection";

/** 管理员模型与网关配置页（/admin/model-settings）；同一表单也嵌入 /settings 供管理员使用。 */
export default function AdminModelSettingsPage() {
  return (
    <div className="mx-auto w-full space-y-5! pt-0!">
      <LlmGatewaySettingsSection />
    </div>
  );
}
