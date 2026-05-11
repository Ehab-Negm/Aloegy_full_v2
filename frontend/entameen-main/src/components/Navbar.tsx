import { useState } from "react";
import { Link } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { motion, AnimatePresence } from "framer-motion";
import { useTranslation } from "react-i18next";

import LanguageToggle from "@/components/LanguageToggle";
import logo from "@/assets/logo.png";

const Navbar = () => {
  const { t } = useTranslation();
  const [mobileOpen, setMobileOpen] = useState(false);

  const navLinks = [
    { label: t("nav.home"), path: "/" },
    { label: t("nav.features"), path: "/#services" },
    { label: t("nav.pricing"), path: "/pricing" },
    { label: t("nav.about"), path: "/about" },
    { label: t("nav.contact"), path: "/#contact" },
  ];

  return (
    <nav className="fixed inset-x-0 top-0 z-50 glass border-b border-border/50">
      <div className="container mx-auto flex items-center justify-between h-16 px-4">
        <Link to="/" className="flex items-center gap-2.5">
          <img src={logo} alt={t("hero.title")} className="w-8 h-8 object-contain" />
          <span className="text-xl font-heading font-bold text-foreground">{t("hero.title")}</span>
        </Link>

        <div className="hidden md:flex items-center gap-7">
          {navLinks.map((link) =>
            link.path.startsWith("/#") ? (
              <a
                key={link.path}
                href={link.path}
                className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                {link.label}
              </a>
            ) : (
              <Link
                key={link.path}
                to={link.path}
                className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                {link.label}
              </Link>
            )
          )}
        </div>

        <div className="hidden md:flex items-center gap-2">
          <LanguageToggle />
          <Link to="/login">
            <Button
              size="sm"
              className="rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand px-5"
            >
              {t("nav.login")}
            </Button>
          </Link>
        </div>

        <button
          className="md:hidden p-2 text-foreground"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label="Toggle navigation menu"
        >
          {mobileOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </div>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="md:hidden glass border-t border-border/50"
          >
            <div className="flex flex-col p-4 gap-1">
              {navLinks.map((link) =>
                link.path.startsWith("/#") ? (
                  <a
                    key={link.path}
                    href={link.path}
                    onClick={() => setMobileOpen(false)}
                    className="py-2.5 px-3 rounded-lg text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
                  >
                    {link.label}
                  </a>
                ) : (
                  <Link
                    key={link.path}
                    to={link.path}
                    onClick={() => setMobileOpen(false)}
                    className="py-2.5 px-3 rounded-lg text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
                  >
                    {link.label}
                  </Link>
                )
              )}
              <div className="mt-2 flex items-center gap-2">
                <LanguageToggle className="flex-shrink-0" />
                <Link to="/login" onClick={() => setMobileOpen(false)} className="flex-1">
                  <Button className="w-full rounded-lg bg-primary text-primary-foreground">
                    {t("nav.login")}
                  </Button>
                </Link>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </nav>
  );
};

export default Navbar;
