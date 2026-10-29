export type AppLanguage = "zh-CN" | "en-US";
export type AppTheme = "light" | "dark" | "system";

export interface AppPreferences {
  language: AppLanguage;
  theme: AppTheme;
}

const STORAGE_KEY = "openair.app.preferences.v1";

export const DEFAULT_APP_PREFERENCES: AppPreferences = {
  language: "zh-CN",
  theme: "system",
};

export function readAppPreferences(): AppPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_APP_PREFERENCES;
    const parsed = JSON.parse(raw) as Partial<AppPreferences>;
    return {
      language: parsed.language === "en-US" ? "en-US" : "zh-CN",
      theme:
        parsed.theme === "light" || parsed.theme === "dark" || parsed.theme === "system"
          ? parsed.theme
          : DEFAULT_APP_PREFERENCES.theme,
    };
  } catch {
    return DEFAULT_APP_PREFERENCES;
  }
}

export function writeAppPreferences(next: AppPreferences): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}

export function resolveSystemTheme(): Exclude<AppTheme, "system"> {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "light";
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolveEffectiveTheme(theme: AppTheme): Exclude<AppTheme, "system"> {
  return theme === "system" ? resolveSystemTheme() : theme;
}
