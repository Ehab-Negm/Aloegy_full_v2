import { useEffect } from "react";
import { useLocation, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useLanguageDirection } from "@/hooks/use-language-direction";

const NotFound = () => {
  useLanguageDirection();
  const location = useLocation();
  const { i18n } = useTranslation();
  const isRTL = (i18n.resolvedLanguage ?? i18n.language ?? "en").startsWith("ar");

  useEffect(() => {
    console.error("404 Error: User attempted to access non-existent route:", location.pathname);
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="text-center">
        <p className="mb-4 font-mono text-xs uppercase tracking-widest text-muted-foreground">
          Error 404
        </p>
        <h1 className="mb-4 text-7xl font-semibold tracking-tight gradient-text-soft">404</h1>
        <p className="mb-8 text-base text-muted-foreground">
          {isRTL ? "عذرًا، الصفحة دي مش موجودة" : "This page doesn't exist."}
        </p>
        <Link to="/">
          <Button className="h-11 rounded-full bg-primary px-7 text-sm font-medium text-primary-foreground glow-brand hover:bg-primary/90">
            {isRTL ? "ارجع للرئيسية" : "Back home"}
          </Button>
        </Link>
      </div>
    </div>
  );
};

export default NotFound;
