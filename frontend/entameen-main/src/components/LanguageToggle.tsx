import { Globe } from "lucide-react";
import { useTranslation } from "react-i18next";

interface LanguageToggleProps {
  className?: string;
}

const LanguageToggle = ({ className = "" }: LanguageToggleProps) => {
  const { i18n } = useTranslation();
  const current = (i18n.resolvedLanguage ?? i18n.language ?? "en").split("-")[0];
  const next = current === "ar" ? "en" : "ar";

  const handleToggle = () => {
    void i18n.changeLanguage(next);
  };

  return (
    <button
      onClick={handleToggle}
      aria-label={`Switch to ${next === "ar" ? "Arabic" : "English"}`}
      title={next === "ar" ? "العربية" : "English"}
      className={`inline-flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground ${className}`}
    >
      <Globe size={16} />
    </button>
  );
};

export default LanguageToggle;
