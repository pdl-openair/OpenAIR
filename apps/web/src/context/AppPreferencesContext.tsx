import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  DEFAULT_APP_PREFERENCES,
  readAppPreferences,
  resolveEffectiveTheme,
  type AppLanguage,
  type AppPreferences,
  type AppTheme,
  writeAppPreferences,
} from "../lib/appPreferences";

interface AppPreferencesContextValue {
  preferences: AppPreferences;
  effectiveTheme: "light" | "dark";
  setLanguage: (language: AppLanguage) => void;
  setTheme: (theme: AppTheme) => void;
}

const AppPreferencesContext = createContext<AppPreferencesContextValue | null>(null);

export function AppPreferencesProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState<AppPreferences>(() => readAppPreferences());
  const [systemThemeTick, setSystemThemeTick] = useState(0);

  useEffect(() => {
    writeAppPreferences(preferences);
  }, [preferences]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemThemeTick((n) => n + 1);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const value = useMemo<AppPreferencesContextValue>(
    () => ({
      preferences,
      effectiveTheme: resolveEffectiveTheme(preferences.theme),
      setLanguage: (language) => setPreferences((prev) => ({ ...prev, language })),
      setTheme: (theme) => setPreferences((prev) => ({ ...prev, theme })),
    }),
    [preferences, systemThemeTick],
  );

  return <AppPreferencesContext.Provider value={value}>{children}</AppPreferencesContext.Provider>;
}

export function useAppPreferences(): AppPreferencesContextValue {
  const ctx = useContext(AppPreferencesContext);
  if (!ctx) {
    return {
      preferences: DEFAULT_APP_PREFERENCES,
      effectiveTheme: "light",
      setLanguage: () => undefined,
      setTheme: () => undefined,
    };
  }
  return ctx;
}
