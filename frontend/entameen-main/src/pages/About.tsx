import { motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  Languages,
  Mail,
  MapPin,
  Server,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import { useLanguageDirection } from "@/hooks/use-language-direction";

const easeSmooth = [0.16, 1, 0.3, 1] as const;

const fadeUpItem = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.6, ease: easeSmooth } },
};

const VALUE_KEYS = ["dialect", "honest", "operators", "infra"] as const;
const VALUE_ICONS = {
  dialect: Languages,
  honest: ShieldCheck,
  operators: Users,
  infra: Server,
} as const;

const About = () => {
  useLanguageDirection();
  const { t, i18n } = useTranslation();

  const isRTL = (i18n.resolvedLanguage ?? i18n.language ?? "en").startsWith("ar");
  const ArrowIcon = isRTL ? ArrowLeft : ArrowRight;

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      {/* Hero */}
      <section className="relative overflow-hidden gradient-hero pt-24 pb-12 sm:pt-28 sm:pb-16">
        <div className="absolute inset-0 pointer-events-none">
          <motion.div
            className="absolute top-10 start-1/3 h-[440px] w-[440px] rounded-full bg-primary/5 blur-[100px]"
            animate={{ x: [0, 30, -10, 0], y: [0, -15, 10, 0] }}
            transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
          />
        </div>

        <div className="container relative mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: easeSmooth }}
            className="mx-auto max-w-3xl text-center"
          >
            <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary/10 px-4 py-1.5 text-xs font-semibold text-primary mb-6">
              <Sparkles size={12} />
              {t("nav.about")}
            </span>
            <h1 className="text-display text-foreground mb-5">{t("aboutPage.heroTitle")}</h1>
            <p className="mx-auto max-w-2xl text-base sm:text-lg text-muted-foreground leading-relaxed">
              {t("aboutPage.heroSubtitle")}
            </p>
          </motion.div>
        </div>
      </section>

      {/* Mission + Story */}
      <section className="border-y border-border/60 bg-card py-16 sm:py-20">
        <div className="container mx-auto">
          <div className="mx-auto grid max-w-5xl items-start gap-12 lg:grid-cols-12">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.6, ease: easeSmooth }}
              className="lg:col-span-5"
            >
              <div className="eyebrow mb-3">{t("aboutPage.missionTitle")}</div>
              <h2 className="text-section text-foreground mb-5">
                {t("aboutPage.missionTitle")}
              </h2>
              <p className="text-base text-muted-foreground leading-relaxed">
                {t("aboutPage.missionText")}
              </p>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.6, ease: easeSmooth, delay: 0.1 }}
              className="lg:col-span-7"
            >
              <div className="eyebrow mb-3">{t("aboutPage.storyTitle")}</div>
              <h2 className="text-section text-foreground mb-5">
                {t("aboutPage.storyTitle")}
              </h2>
              <div className="space-y-4 text-base text-muted-foreground leading-relaxed">
                <p>{t("aboutPage.storyP1")}</p>
                <p>{t("aboutPage.storyP2")}</p>
                <p>{t("aboutPage.storyP3")}</p>
              </div>
            </motion.div>
          </div>
        </div>
      </section>

      {/* Values */}
      <section className="py-16 sm:py-20">
        <div className="container mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.6, ease: easeSmooth }}
            className="mx-auto mb-12 max-w-2xl text-center"
          >
            <div className="eyebrow mb-3">{t("aboutPage.valuesTitle")}</div>
            <h2 className="text-section text-foreground">{t("aboutPage.valuesTitle")}</h2>
          </motion.div>

          <motion.div
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true, margin: "-60px" }}
            variants={{
              hidden: {},
              visible: { transition: { staggerChildren: 0.07 } },
            }}
            className="mx-auto grid max-w-5xl gap-4 md:grid-cols-2"
          >
            {VALUE_KEYS.map((key) => {
              const Icon = VALUE_ICONS[key];
              return (
                <motion.div
                  key={key}
                  variants={fadeUpItem}
                  whileHover={{ y: -4 }}
                  transition={{ duration: 0.25, ease: easeSmooth }}
                  className="rounded-2xl border border-border/60 bg-card p-6 transition-all duration-400 ease-smooth hover:shadow-elevated hover:border-primary/30"
                >
                  <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <Icon size={20} strokeWidth={1.75} />
                  </div>
                  <h3 className="text-card-title mb-2 text-foreground">
                    {t(`aboutPage.values.${key}.title`)}
                  </h3>
                  <p className="text-body-card text-muted-foreground">
                    {t(`aboutPage.values.${key}.desc`)}
                  </p>
                </motion.div>
              );
            })}
          </motion.div>
        </div>
      </section>

      {/* CTA */}
      <section className="border-t border-border/60 bg-card py-16 sm:py-20">
        <div className="container mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.6, ease: easeSmooth }}
            className="mx-auto max-w-2xl text-center"
          >
            <h2 className="text-section text-foreground mb-4">{t("aboutPage.ctaTitle")}</h2>
            <p className="mb-8 text-base text-muted-foreground">{t("aboutPage.ctaSubtitle")}</p>

            <div className="flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link to="/#contact" className="w-full sm:w-auto">
                <Button
                  size="lg"
                  className="group h-12 w-full gap-2 rounded-xl bg-primary px-8 text-sm font-semibold text-primary-foreground shadow-brand transition-all duration-250 ease-smooth hover:bg-primary/90 hover:shadow-elevated hover:-translate-y-0.5"
                >
                  {t("aboutPage.ctaButton")}
                  <ArrowIcon
                    size={16}
                    className="transition-transform duration-250 ease-smooth group-hover:translate-x-0.5"
                  />
                </Button>
              </Link>
              <a href="mailto:contact@aloegy.ai" className="w-full sm:w-auto">
                <Button
                  size="lg"
                  variant="outline"
                  className="h-12 w-full gap-2 rounded-xl border-border px-7 text-sm font-semibold transition-all duration-250 ease-smooth hover:bg-muted/60 hover:-translate-y-0.5"
                >
                  <Mail size={16} />
                  contact@aloegy.ai
                </Button>
              </a>
            </div>
            <div className="mt-6 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <MapPin size={12} />
              <span>{t("footer.address")}</span>
            </div>
          </motion.div>
        </div>
      </section>

      <Footer />
    </div>
  );
};

export default About;
