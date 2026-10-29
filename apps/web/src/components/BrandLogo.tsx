import { useAppPreferences } from "../context/AppPreferencesContext";
import { cn } from "../lib/cn";

const LOGO_LIGHT = "/brand/openair-logo.svg";
const LOGO_DARK = "/brand/openair-logo-on-dark.svg";

export function brandLogoSrc(theme: "light" | "dark" = "light") {
  return theme === "dark" ? LOGO_DARK : LOGO_LIGHT;
}

export function BrandLogo({
  className,
  alt = "OpenAIR",
}: {
  className?: string;
  alt?: string;
}) {
  const { effectiveTheme } = useAppPreferences();
  return (
    <img
      src={brandLogoSrc(effectiveTheme)}
      alt={alt}
      draggable={false}
      className={cn("block object-contain", className)}
    />
  );
}
