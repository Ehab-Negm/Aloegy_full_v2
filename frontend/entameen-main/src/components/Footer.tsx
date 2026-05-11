import { Mail, MapPin, Linkedin } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import logo from "@/assets/logo.png";

const Footer = () => {
  const { t } = useTranslation();

  return (
    <footer className="bg-foreground text-background">
      <div className="container mx-auto px-4 py-14">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-10">
          <div>
            <div className="flex items-center gap-2.5 mb-4">
              <img src={logo} alt={t("hero.title")} className="w-8 h-8 object-contain" />
              <h3 className="text-xl font-heading font-bold">{t("hero.title")}</h3>
            </div>
            <p className="text-sm font-medium mb-1 text-background/80">{t("footer.tagline")}</p>
            <p className="text-background/60 text-sm leading-relaxed">{t("footer.description")}</p>
          </div>

          <div>
            <h4 className="font-heading font-semibold mb-4">{t("footer.quickLinks")}</h4>
            <ul className="space-y-2.5 text-sm text-background/60">
              <li>
                <Link to="/" className="hover:text-background transition-colors">
                  {t("nav.home")}
                </Link>
              </li>
              <li>
                <a href="/#services" className="hover:text-background transition-colors">
                  {t("nav.features")}
                </a>
              </li>
              <li>
                <Link to="/pricing" className="hover:text-background transition-colors">
                  {t("nav.pricing")}
                </Link>
              </li>
              <li>
                <Link to="/about" className="hover:text-background transition-colors">
                  {t("nav.about")}
                </Link>
              </li>
              <li>
                <a href="/#contact" className="hover:text-background transition-colors">
                  {t("nav.contact")}
                </a>
              </li>
            </ul>
          </div>

          <div>
            <h4 className="font-heading font-semibold mb-4">{t("footer.contactUs")}</h4>
            <div className="space-y-3 text-sm text-background/60">
              <a
                href="mailto:contact@aloegy.ai"
                className="flex items-center gap-2.5 hover:text-background transition-colors"
              >
                <Mail size={15} className="text-brand-glow" />
                <span dir="ltr">contact@aloegy.ai</span>
              </a>
              <div className="flex items-center gap-2.5">
                <MapPin size={15} className="text-brand-glow" />
                <span>{t("footer.address")}</span>
              </div>
              <a
                href="https://www.linkedin.com/company/%D8%A3%D9%84%D9%88-%D8%A5%D9%8A%DA%86%D9%8A/"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2.5 hover:text-background transition-colors"
              >
                <Linkedin size={15} className="text-brand-glow" />
                <span>LinkedIn</span>
              </a>
            </div>
          </div>
        </div>

        <div className="mt-10 pt-6 border-t border-background/10 flex flex-col md:flex-row items-center justify-between gap-4 text-sm text-background/40">
          <p>
            &copy; {new Date().getFullYear()} {t("hero.title")}. {t("footer.rights")}
          </p>
          <div className="flex items-center gap-5">
            <Link to="/privacy" className="hover:text-background transition-colors">
              {t("footer.privacy")}
            </Link>
            <Link to="/terms" className="hover:text-background transition-colors">
              {t("footer.terms")}
            </Link>
          </div>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
