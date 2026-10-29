import { useEffect, useMemo, useState } from "react";
import { AppstoreOutlined, BgColorsOutlined, GlobalOutlined } from "@ant-design/icons";
import { Alert, Card, Col, Descriptions, Radio, Row, Space, Typography } from "antd";
import { fetchHealth, getApiBaseUrl, type RuntimeVersion } from "../api/client";
import { useAppPreferences } from "../context/AppPreferencesContext";
import QuotaCard from "../components/QuotaCard";
import { cn } from "../lib/cn";
import { webRuntimeVersion } from "../lib/runtimeVersion";

const COPY = {
  "zh-CN": {
    title: "设置",
    subtitle: "管理系统语言与主题偏好，修改后立即生效。",
    languageTitle: "系统语言",
    languageDesc: "控制导航、工作台、看板和详情页的界面文案。",
    themeTitle: "系统主题",
    themeDesc: "控制整体外观，支持浅色、深色和跟随系统。",
    zh: "中文",
    en: "英文",
    light: "浅色",
    dark: "深色",
    system: "跟随系统",
    hint: "这些设置将保存在当前浏览器中。",
  },
  "en-US": {
    title: "Settings",
    subtitle: "Manage language and theme preferences. Changes apply immediately.",
    languageTitle: "Language",
    languageDesc: "Controls UI copy across navigation, workbench, board, and detail pages.",
    themeTitle: "Theme",
    themeDesc: "Controls the overall appearance with light, dark, or system mode.",
    zh: "Chinese",
    en: "English",
    light: "Light",
    dark: "Dark",
    system: "System",
    hint: "These settings are stored in the current browser.",
  },
} as const;

const settingsCardClass = cn(
  "h-full rounded-card border border-border! bg-surface! shadow-sm",
  "[&_.ant-card-head]:border-border",
);

function versionValue(value: string | null | undefined): string {
  return value?.trim() || "未提供";
}

export default function SettingsPage() {
  const { preferences, setLanguage, setTheme } = useAppPreferences();
  const copy = COPY[preferences.language];
  const [apiVersion, setApiVersion] = useState<RuntimeVersion | null>(null);
  const [apiVersionError, setApiVersionError] = useState(false);
  const webVersion = useMemo(() => webRuntimeVersion(), []);

  useEffect(() => {
    let disposed = false;
    fetchHealth()
      .then((health) => {
        if (disposed) return;
        setApiVersion(health.runtimeVersion ?? null);
        setApiVersionError(false);
      })
      .catch(() => {
        if (disposed) return;
        setApiVersion(null);
        setApiVersionError(true);
      });
    return () => {
      disposed = true;
    };
  }, []);

  return (
    <div className="mx-auto w-full space-y-5! pt-0!">
      {/* <div className="mb-5">
        <Typography.Title level={2} className="mb-2 mt-0!">
          {copy.title}
        </Typography.Title>
        <Typography.Paragraph type="secondary" className="mb-0 text-base!">
          {copy.subtitle}
        </Typography.Paragraph>
      </div> */}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card className={settingsCardClass} title={<Space><GlobalOutlined />{copy.languageTitle}</Space>}>
            <Typography.Paragraph type="secondary">{copy.languageDesc}</Typography.Paragraph>
            <Radio.Group
              value={preferences.language}
              onChange={(e) => setLanguage(e.target.value)}
              className="w-full"
            >
              <Space direction="vertical" size={12}>
                <Radio value="zh-CN">{copy.zh}</Radio>
                <Radio value="en-US">{copy.en}</Radio>
              </Space>
            </Radio.Group>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card className={settingsCardClass} title={<Space><BgColorsOutlined />{copy.themeTitle}</Space>}>
            <Typography.Paragraph type="secondary">{copy.themeDesc}</Typography.Paragraph>
            <Radio.Group
              value={preferences.theme}
              onChange={(e) => setTheme(e.target.value)}
              className="w-full"
            >
              <Space direction="vertical" size={12}>
                <Radio value="light">{copy.light}</Radio>
                <Radio value="dark">{copy.dark}</Radio>
                <Radio value="system">{copy.system}</Radio>
              </Space>
            </Radio.Group>
          </Card>
        </Col>
      </Row>

      <Card
        className={cn(settingsCardClass)}
        title={<Space><AppstoreOutlined />运行版本</Space>}
      >
        <Typography.Paragraph type="secondary">
          用于确认当前页面连接的 API、后端启动时间和前后端代码版本。
        </Typography.Paragraph>
        {apiVersionError ? (
          <Alert
            className="mb-4"
            type="warning"
            showIcon
            message="暂时无法读取 API 运行版本，请确认后端服务是否启动。"
          />
        ) : null}
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Descriptions
              bordered
              size="small"
              column={1}
              title="API 运行时"
              items={[
                { key: "apiBase", label: "API 地址", children: getApiBaseUrl() || "same-origin" },
                { key: "apiBranch", label: "分支", children: versionValue(apiVersion?.gitBranch) },
                { key: "apiCommit", label: "Commit", children: versionValue(apiVersion?.gitCommitShort) },
                { key: "apiStarted", label: "启动时间", children: versionValue(apiVersion?.startedAt) },
                { key: "apiBuild", label: "构建时间", children: versionValue(apiVersion?.buildTime) },
              ]}
            />
          </Col>
          <Col xs={24} lg={12}>
            <Descriptions
              bordered
              size="small"
              column={1}
              title="Web 运行时"
              items={[
                { key: "webMode", label: "模式", children: webVersion.mode },
                { key: "webBranch", label: "分支", children: versionValue(webVersion.gitBranch) },
                { key: "webCommit", label: "Commit", children: versionValue(webVersion.gitCommitShort) },
                { key: "webStarted", label: "启动时间", children: versionValue(webVersion.startedAt) },
                { key: "webBuild", label: "构建时间", children: versionValue(webVersion.buildTime) },
              ]}
            />
          </Col>
        </Row>
      </Card>

      {/* <div>
        <SkillManager language={preferences.language} />
      </div> */}

      {/* <ContributionPanel /> */}

      <QuotaCard />

      {/* <Card className={cn(settingsCardClass, "mt-4")} size="small">
        <Space>
          <AppstoreOutlined />
          <Typography.Text type="secondary">{copy.hint}</Typography.Text>
        </Space>
      </Card> */}
    </div>
  );
}
