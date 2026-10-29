/**
 * Ant Design seed tokens aligned with --wb-* in index.css.
 * Pass literal colors (not CSS var()) so theme algorithms can derive hover/active tokens.
 */
export type AntThemeMode = "light" | "dark";

const LIGHT_TOKENS = {
  colorPrimary: "#594AFF",
  colorInfo: "#594AFF",
  colorSuccess: "#20AE70",
  colorWarning: "#C98812",
  colorError: "#DF5A62",
  borderRadius: 8,
  colorBgContainer: "#ffffff",
  colorBgLayout: "#F5F6FA",
  colorBgElevated: "#ffffff",
  colorText: "#2B2D36",
  colorTextSecondary: "#666A78",
  colorTextTertiary: "#969BAA",
  colorBorder: "#E8EAF0",
  colorBorderSecondary: "#F0F2F7"
} as const;

const DARK_TOKENS = {
  colorPrimary: "#A497FF",
  colorInfo: "#A497FF",
  colorSuccess: "#55C893",
  colorWarning: "#E7B55C",
  colorError: "#F07A82",
  borderRadius: 8,
  colorBgContainer: "#201E28",
  colorBgLayout: "#17151E",
  colorBgElevated: "#282532",
  colorText: "#F4F2F8",
  colorTextSecondary: "#CBC7D4",
  colorTextTertiary: "#9892A5",
  colorBorder: "#393648",
  colorBorderSecondary: "#4B465B"
} as const;

export function getAntDesignSeedTokens(mode: AntThemeMode) {
  return mode === "dark" ? DARK_TOKENS : LIGHT_TOKENS;
}
