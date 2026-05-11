import { useState } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Mic,
  PhoneCall,
  PhoneIncoming,
  TrendingUp,
  Utensils,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import VoiceAssistantWidget from "@/components/VoiceAssistantWidget";
import { useLanguageDirection } from "@/hooks/use-language-direction";
import { submitContactForm } from "@/services/api";
import logo from "@/assets/logo.png";
import mascot from "@/assets/aloegy-mascot.png";

const fadeUp = {
  hidden: { opacity: 0, y: 30 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.1, duration: 0.5, ease: "easeOut" as const },
  }),
};

const useDirectionalArrow = () => {
  const { i18n } = useTranslation();
  const isRTL = (i18n.resolvedLanguage ?? i18n.language ?? "en").startsWith("ar");
  return isRTL ? ArrowLeft : ArrowRight;
};

/* -------------------------------------------------------------------------- */
/* Hero mascot                                                                */
/* -------------------------------------------------------------------------- */

const HeroMascot = () => {
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const parallaxX = useSpring(useTransform(mx, [-0.5, 0.5], [-8, 8]), { stiffness: 70, damping: 16 });
  const parallaxY = useSpring(useTransform(my, [-0.5, 0.5], [-8, 8]), { stiffness: 70, damping: 16 });

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    mx.set((e.clientX - rect.left) / rect.width - 0.5);
    my.set((e.clientY - rect.top) / rect.height - 0.5);
  };
  const handleMouseLeave = () => {
    mx.set(0);
    my.set(0);
  };

  return (
    <div
      className="relative flex items-center justify-center"
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{ perspective: 1200 }}
    >
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          className="absolute rounded-full border border-primary/30"
          initial={{ width: 170, height: 170, opacity: 0 }}
          animate={{
            width: [170, 280, 340],
            height: [170, 280, 340],
            opacity: [0.5, 0.18, 0],
          }}
          transition={{ duration: 2.8, repeat: Infinity, delay: i * 0.9, ease: "easeOut" }}
        />
      ))}

      <motion.div
        className="absolute w-[300px] h-[300px] rounded-full bg-primary/20 blur-[70px] pointer-events-none"
        animate={{ opacity: [0.6, 0.9, 0.6] }}
        transition={{ duration: 2.6, repeat: Infinity, ease: "easeInOut" }}
      />

      <motion.div style={{ x: parallaxX, y: parallaxY }} className="relative z-10">
        <motion.div
          animate={{
            y: [0, -12, -4, -10, 0],
            rotate: [-2.5, 2.5, -1.5, 2, -2.5],
          }}
          transition={{
            y: { duration: 3.4, repeat: Infinity, ease: "easeInOut" },
            rotate: { duration: 5.2, repeat: Infinity, ease: "easeInOut" },
          }}
        >
          <motion.div
            animate={{ scale: [1, 1.035, 1] }}
            transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
          >
            <img
              src={mascot}
              alt="AloEgy mascot"
              className="w-[180px] md:w-[230px] drop-shadow-[0_20px_40px_rgba(59,130,246,0.4)] select-none"
              draggable={false}
            />
          </motion.div>
        </motion.div>
      </motion.div>
    </div>
  );
};

/* -------------------------------------------------------------------------- */
/* Index                                                                      */
/* -------------------------------------------------------------------------- */

const Index = () => {
  useLanguageDirection();
  const { t } = useTranslation();
  const ArrowIcon = useDirectionalArrow();

  const services = [
    { icon: PhoneCall, key: "voiceAgent" },
    { icon: PhoneIncoming, key: "noMissedCalls" },
    { icon: TrendingUp, key: "upsell" },
    { icon: Utensils, key: "posIntegration" },
    { icon: BarChart3, key: "analytics" },
    { icon: Zap, key: "alwaysOn" },
  ] as const;

  const stats = [
    { key: "egyptianArabic" },
    { key: "concurrent" },
    { key: "alwaysOn" },
    { key: "latency" },
  ] as const;

  const howSteps = [
    { step: "١", key: "step1" },
    { step: "٢", key: "step2" },
    { step: "٣", key: "step3" },
  ] as const;

  const [contactForm, setContactForm] = useState({ restaurantName: "", phone: "", message: "" });
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleContactSubmit = async () => {
    if (!contactForm.restaurantName || !contactForm.phone) return;
    setSubmitting(true);
    try {
      await submitContactForm(contactForm);
      setSubmitted(true);
      setContactForm({ restaurantName: "", phone: "", message: "" });
    } catch (e) {
      console.error("Contact form error:", e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      {/* Hero Section */}
      <section className="relative pt-24 pb-20 overflow-hidden gradient-hero">
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-10 start-1/4 w-[500px] h-[500px] rounded-full bg-primary/4 blur-[100px]" />
          <div className="absolute bottom-0 end-1/4 w-[400px] h-[400px] rounded-full bg-brand-glow/5 blur-[80px]" />
        </div>

        <div className="container mx-auto px-4 relative">
          <div className="grid md:grid-cols-2 gap-10 items-center">
            {/* Text column */}
            <motion.div
              initial={{ opacity: 0, y: 40 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7 }}
              className="text-center md:text-start"
            >
              <motion.span
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.2 }}
                className="inline-block px-4 py-1.5 rounded-full text-xs font-semibold bg-primary/10 text-primary mb-6 border border-primary/15"
              >
                {t("hero.badge")}
              </motion.span>

              <h1 className="text-5xl md:text-7xl font-heading font-extrabold text-foreground leading-tight mb-4">
                {t("hero.title")}
              </h1>

              <p className="text-2xl md:text-3xl font-heading font-semibold text-foreground/75 mb-2">
                {t("hero.tagline")}
              </p>
              <p className="text-lg text-muted-foreground mb-3">
                {t("hero.subtitle")}
              </p>
              <p className="text-base text-muted-foreground mb-8 max-w-xl md:mx-0 mx-auto leading-relaxed">
                {t("hero.description")}
              </p>
              <div className="flex flex-col sm:flex-row gap-3 md:justify-start justify-center">
                <Button
                  size="lg"
                  onClick={() => window.dispatchEvent(new Event("aloegy:open-voice"))}
                  className="rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand px-8 gap-2 h-12"
                >
                  <Mic size={18} />
                  {t("hero.tryVoice")}
                </Button>
                <a href="#services">
                  <Button size="lg" variant="outline" className="rounded-lg border-border px-8 h-12 hover:bg-muted/50 gap-2 w-full sm:w-auto">
                    {t("hero.learnMore")}
                    <ArrowIcon size={18} />
                  </Button>
                </a>
              </div>
            </motion.div>

            {/* Mascot column */}
            <motion.div
              initial={{ opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.8, delay: 0.2 }}
              className="flex justify-center md:justify-end"
            >
              <HeroMascot />
            </motion.div>
          </div>
        </div>
      </section>

      {/* Stats */}
      <section className="py-16 border-y border-border/50 bg-card">
        <div className="container mx-auto px-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
            {stats.map((item, i) => (
              <motion.div
                key={item.key}
                custom={i}
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUp}
                className="text-center"
              >
                <div className="text-4xl md:text-5xl font-heading font-extrabold text-primary mb-1">
                  {t(`stats.${item.key}.value`)}
                </div>
                <div className="font-heading font-semibold text-foreground text-sm mb-0.5">
                  {t(`stats.${item.key}.label`)}
                </div>
                <div className="text-xs text-muted-foreground">
                  {t(`stats.${item.key}.desc`)}
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Services */}
      <section id="services" className="py-24">
        <div className="container mx-auto px-4">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-center mb-16"
          >
            <h2 className="text-3xl md:text-5xl font-heading font-bold mb-4 text-foreground">
              {t("services.title")}
            </h2>
            <p className="text-muted-foreground max-w-md mx-auto">
              {t("services.subtitle")}
            </p>
          </motion.div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {services.map((service, i) => (
              <motion.div
                key={service.key}
                custom={i}
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUp}
              >
                <Card className="group hover:shadow-elevated transition-all duration-300 border-border/60 bg-card h-full">
                  <CardContent className="p-6">
                    <div className="w-11 h-11 rounded-lg bg-primary/10 flex items-center justify-center mb-4 group-hover:bg-primary group-hover:shadow-brand transition-all duration-300">
                      <service.icon size={22} className="text-primary group-hover:text-primary-foreground transition-colors" />
                    </div>
                    <h3 className="font-heading font-semibold text-lg mb-2 text-foreground">
                      {t(`services.items.${service.key}.title`)}
                    </h3>
                    <p className="text-sm text-muted-foreground leading-relaxed">
                      {t(`services.items.${service.key}.description`)}
                    </p>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* About */}
      <section id="about" className="py-24 bg-card border-y border-border/50">
        <div className="container mx-auto px-4">
          <div className="grid md:grid-cols-2 gap-16 items-center">
            <motion.div
              initial={{ opacity: 0, x: 40 }}
              whileInView={{ opacity: 1, x: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6 }}
            >
              <h2 className="text-3xl md:text-5xl font-heading font-bold mb-6 text-foreground">
                {t("about.title")}
              </h2>
              <p className="text-muted-foreground leading-relaxed mb-4">{t("about.p1")}</p>
              <p className="text-muted-foreground leading-relaxed mb-4">{t("about.p2")}</p>
              <p className="text-muted-foreground leading-relaxed mb-8">{t("about.p3")}</p>
              <Link
                to="/about"
                className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:text-primary/80 transition-colors"
              >
                {t("nav.about")}
                <ArrowUpRight size={14} />
              </Link>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, x: -40 }}
              whileInView={{ opacity: 1, x: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6 }}
              className="relative"
            >
              <div className="aspect-square rounded-2xl bg-gradient-to-br from-primary/8 to-accent border border-border/60 p-1">
                <div className="w-full h-full rounded-xl bg-card flex items-center justify-center shadow-soft">
                  <div className="text-center p-8">
                    <motion.img
                      src={logo}
                      alt={t("hero.title")}
                      className="w-28 h-28 mx-auto mb-5 object-contain drop-shadow-md"
                      animate={{ rotate: [0, 3, -3, 0] }}
                      transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
                    />
                    <p className="font-heading font-bold text-xl text-foreground">{t("hero.title")}</p>
                    <p className="text-sm text-muted-foreground mt-1">{t("hero.tagline")}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{t("hero.badge")}</p>
                  </div>
                </div>
              </div>
            </motion.div>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="py-24">
        <div className="container mx-auto px-4">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-center mb-16"
          >
            <h2 className="text-3xl md:text-5xl font-heading font-bold mb-4 text-foreground">
              {t("howItWorks.title")}
            </h2>
          </motion.div>

          <div className="grid md:grid-cols-3 gap-10 max-w-4xl mx-auto">
            {howSteps.map((item, i) => (
              <motion.div
                key={item.step}
                custom={i}
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUp}
                className="text-center"
              >
                <div className="w-14 h-14 rounded-full bg-primary text-primary-foreground flex items-center justify-center mx-auto mb-4 text-xl font-bold shadow-brand">
                  {item.step}
                </div>
                <h3 className="font-heading font-semibold text-lg mb-2 text-foreground">
                  {t(`howItWorks.${item.key}.title`)}
                </h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {t(`howItWorks.${item.key}.desc`)}
                </p>
              </motion.div>
            ))}
          </div>

          <div className="text-center mt-14">
            <Link to="/pricing">
              <Button size="lg" className="rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand px-10 gap-2 text-lg h-13">
                {t("howItWorks.cta")}
                <ArrowIcon size={20} />
              </Button>
            </Link>
          </div>
        </div>
      </section>

      {/* Contact */}
      <section id="contact" className="py-24 bg-card border-t border-border/50">
        <div className="container mx-auto px-4">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="max-w-lg mx-auto text-center"
          >
            <h2 className="text-3xl md:text-5xl font-heading font-bold mb-4 text-foreground">
              {t("contact.title")}
            </h2>
            <p className="text-muted-foreground mb-8">{t("contact.subtitle")}</p>
            <div className="bg-background rounded-2xl p-8 shadow-elevated border border-border/60">
              {submitted ? (
                <div className="text-center py-8">
                  <div className="w-14 h-14 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
                    <svg className="w-7 h-7 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  </div>
                  <p className="text-lg font-heading font-semibold text-foreground mb-2">
                    {t("contact.successTitle")}
                  </p>
                  <p className="text-sm text-muted-foreground">{t("contact.successDesc")}</p>
                </div>
              ) : (
                <div className="space-y-4">
                  <input
                    value={contactForm.restaurantName}
                    onChange={(e) => setContactForm((p) => ({ ...p, restaurantName: e.target.value }))}
                    placeholder={t("contact.restaurantName")}
                    className="w-full px-4 py-3 rounded-lg bg-muted/50 border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30 transition-all"
                  />
                  <input
                    value={contactForm.phone}
                    onChange={(e) => setContactForm((p) => ({ ...p, phone: e.target.value }))}
                    placeholder={t("contact.phone")}
                    className="w-full px-4 py-3 rounded-lg bg-muted/50 border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30 transition-all"
                    dir="ltr"
                  />
                  <textarea
                    value={contactForm.message}
                    onChange={(e) => setContactForm((p) => ({ ...p, message: e.target.value }))}
                    placeholder={t("contact.message")}
                    rows={4}
                    className="w-full px-4 py-3 rounded-lg bg-muted/50 border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30 transition-all resize-none"
                  />
                  <Button
                    onClick={handleContactSubmit}
                    disabled={submitting || !contactForm.restaurantName || !contactForm.phone}
                    className="w-full rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand h-12"
                  >
                    {submitting ? t("contact.submitting") : t("contact.submit")}
                  </Button>
                </div>
              )}
            </div>
          </motion.div>
        </div>
      </section>

      <Footer />
      <VoiceAssistantWidget />
    </div>
  );
};

export default Index;
