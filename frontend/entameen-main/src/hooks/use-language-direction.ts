import { useEffect } from "react";
import { useTranslation } from "react-i18next";

// Keep <html lang> and <html dir> in sync with the active i18n language so
// CSS direction-aware utilities (start/end, tailwind logical props) and
// screen readers behave correctly when the user toggles between languages.
export const useLanguageDirection = () => {
  const { i18n } = useTranslation();

  useEffect(() => {
    const lang = (i18n.resolvedLanguage ?? i18n.language ?? "en").split("-")[0];
    const dir = lang === "ar" ? "rtl" : "ltr";
    const root = document.documentElement;
    if (root.getAttribute("lang") !== lang) {
      root.setAttribute("lang", lang);
    }
    if (root.getAttribute("dir") !== dir) {
      root.setAttribute("dir", dir);
    }
  }, [i18n.language, i18n.resolvedLanguage]);
};
