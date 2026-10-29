import { ConfigProvider, Empty, theme as antdTheme } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import { StrictMode, useEffect, useMemo } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import {
  AppPreferencesProvider,
  useAppPreferences
} from "./context/AppPreferencesContext";
import { getAntDesignSeedTokens } from "./lib/antDesignTheme";
import "./index.css";
import "./styles/ant-design-overrides.css";

function AppProviders() {
  const { preferences, effectiveTheme } = useAppPreferences();

  useEffect(() => {
    document.documentElement.dataset.theme = effectiveTheme;
    document.documentElement.lang =
      preferences.language === "en-US" ? "en" : "zh-CN";
  }, [effectiveTheme, preferences.language]);

  const locale = preferences.language === "en-US" ? enUS : zhCN;
  const algorithm =
    effectiveTheme === "dark"
      ? antdTheme.darkAlgorithm
      : antdTheme.defaultAlgorithm;

  const configTheme = useMemo(
    () => ({
      cssVar: {
        //  prefix: "openair",
        key: effectiveTheme
      },
      algorithm,
      token: getAntDesignSeedTokens(effectiveTheme)
    }),
    [algorithm, effectiveTheme]
  );

  return (
    <ConfigProvider
      locale={locale}
      theme={configTheme}
      renderEmpty={() => <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    >
      <App />
    </ConfigProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppPreferencesProvider>
      <AppProviders />
    </AppPreferencesProvider>
  </StrictMode>
);
