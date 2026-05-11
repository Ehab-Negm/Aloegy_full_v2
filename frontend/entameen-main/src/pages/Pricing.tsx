import { motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Headphones,
  MessageSquare,
  PhoneCall,
  Sparkles,
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

interface PlanDef {
  key: "starter" | "pro" | "enterprise";
  icon: typeof PhoneCall;
  price: number | null;
  oldPrice: number | null;
  popular: boolean;
}

const PLAN_DEFS: PlanDef[] = [
  { key: "starter", icon: PhoneCall, price: 4000, oldPrice: 6000, popular: false },
  { key: "pro", icon: Headphones, price: 6000, oldPrice: 8000, popular: true },
  { key: "enterprise", icon: MessageSquare, price: null, oldPrice: null, popular: false },
];

const FAQ_KEYS = ["moreSales", "noMissed", "saveSalary", "trueDialect"] as const;

const Pricing = () => {
  useLanguageDirection();
  const { t, i18n } = useTranslation();

  const isRTL = (i18n.resolvedLanguage ?? i18n.language ?? "en").startsWith("ar");
  const ArrowIcon = isRTL ? ArrowLeft : ArrowRight;
  const numberFormatter = new Intl.NumberFormat(isRTL ? "ar-EG" : "en-US");
  const formatPrice = (n: number | null) => (n === null ? null : numberFormatter.format(n));

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      {/* Hero */}
      <section className="relative overflow-hidden gradient-hero pt-24 pb-10 sm:pt-28 sm:pb-14">
        <div className="absolute inset-0 pointer-events-none">
          <motion.div
            className="absolute top-0 start-1/3 h-[400px] w-[400px] rounded-full bg-primary/5 blur-[100px]"
            animate={{ x: [0, 30, -10, 0], y: [0, -15, 10, 0] }}
            transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
          />
        </div>

        <div className="container relative mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: easeSmooth }}
            className="mx-auto max-w-2xl text-center"
          >
            <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary/10 px-4 py-1.5 text-xs font-semibold text-primary mb-6">
              <Sparkles size={12} />
              {t("nav.pricing")}
            </span>
            <h1 className="text-display text-foreground mb-4">{t("pricing.pageTitle")}</h1>
            <p className="text-base sm:text-lg text-muted-foreground leading-relaxed">
              {t("pricing.pageSubtitle")}
            </p>
          </motion.div>
        </div>
      </section>

      {/* Plans */}
      <section className="pb-16 sm:pb-20">
        <div className="container mx-auto">
          <motion.div
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true, margin: "-60px" }}
            variants={{
              hidden: {},
              visible: { transition: { staggerChildren: 0.1 } },
            }}
            className="mx-auto grid max-w-5xl grid-cols-1 gap-4 md:grid-cols-3"
          >
            {PLAN_DEFS.map((plan) => {
              const features = (
                t(`pricing.plans.${plan.key}.features`, { returnObjects: true }) as string[]
              );
              const featureList = Array.isArray(features) ? features : [];
              const formattedPrice = formatPrice(plan.price);
              const formattedOld = formatPrice(plan.oldPrice);

              return (
                <motion.div
                  key={plan.key}
                  variants={fadeUpItem}
                  whileHover={{ y: -4 }}
                  transition={{ duration: 0.25, ease: easeSmooth }}
                  className="relative"
                >
                  {plan.popular && (
                    <div className="absolute -top-3 left-1/2 z-10 -translate-x-1/2">
                      <span className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-primary-foreground shadow-brand">
                        <Sparkles size={11} />
                        {t("pricing.popular")}
                      </span>
                    </div>
                  )}

                  <div
                    className={`flex h-full flex-col rounded-2xl border bg-card p-7 transition-all duration-400 ease-smooth ${
                      plan.popular
                        ? "border-primary/40 shadow-elevated ring-1 ring-primary/15"
                        : "border-border/60 hover:shadow-elevated hover:border-primary/30"
                    }`}
                  >
                    <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary">
                      <plan.icon size={20} strokeWidth={1.75} />
                    </div>

                    <h3 className="text-xl font-bold tracking-tight text-foreground">
                      {t(`pricing.plans.${plan.key}.name`)}
                    </h3>
                    <p className="mt-1.5 min-h-[42px] text-sm text-muted-foreground leading-relaxed">
                      {t(`pricing.plans.${plan.key}.description`)}
                    </p>

                    <div className="mt-6 mb-6 border-t border-border/60 pt-6">
                      {formattedOld && (
                        <p className="text-sm text-muted-foreground/70 line-through">
                          {formattedOld}
                        </p>
                      )}
                      <div className="flex items-baseline gap-1.5">
                        {formattedPrice ? (
                          <>
                            <span className="text-4xl font-bold tracking-tight text-foreground">
                              {formattedPrice}
                            </span>
                            <span className="text-sm text-muted-foreground">
                              {t("pricing.perMonth")}
                            </span>
                          </>
                        ) : (
                          <span className="text-2xl font-bold tracking-tight text-foreground">
                            {t(`pricing.plans.${plan.key}.priceLabel`)}
                          </span>
                        )}
                      </div>
                    </div>

                    <ul className="mb-7 flex-1 space-y-2.5">
                      {featureList.map((feature) => (
                        <li key={feature} className="flex items-start gap-2.5 text-sm">
                          <Check
                            size={14}
                            strokeWidth={2.5}
                            className="mt-0.5 shrink-0 text-primary"
                          />
                          <span className="text-foreground/80 leading-relaxed">{feature}</span>
                        </li>
                      ))}
                    </ul>

                    <Link to="/#contact" className="mt-auto">
                      <Button
                        className={`h-11 w-full rounded-xl text-sm font-semibold transition-all duration-250 ease-smooth ${
                          plan.popular
                            ? "bg-primary text-primary-foreground shadow-brand hover:bg-primary/90 hover:shadow-elevated"
                            : "bg-secondary text-foreground hover:bg-secondary/80"
                        }`}
                      >
                        {plan.price === null ? t("pricing.contactSales") : t("pricing.subscribe")}
                      </Button>
                    </Link>
                  </div>
                </motion.div>
              );
            })}
          </motion.div>

          {/* FAQ */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.6, ease: easeSmooth }}
            className="mx-auto mt-24 max-w-3xl"
          >
            <div className="text-center mb-10">
              <div className="eyebrow mb-3">FAQ</div>
              <h2 className="text-section text-foreground">{t("pricing.faqTitle")}</h2>
            </div>
            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true, margin: "-60px" }}
              variants={{
                hidden: {},
                visible: { transition: { staggerChildren: 0.07 } },
              }}
              className="grid gap-4 md:grid-cols-2"
            >
              {FAQ_KEYS.map((key) => (
                <motion.div
                  key={key}
                  variants={fadeUpItem}
                  whileHover={{ y: -3 }}
                  transition={{ duration: 0.25, ease: easeSmooth }}
                  className="rounded-2xl border border-border/60 bg-card p-6 transition-all duration-400 ease-smooth hover:shadow-elevated hover:border-primary/30"
                >
                  <h4 className="mb-2 text-card-title text-foreground">
                    {t(`pricing.faqs.${key}.title`)}
                  </h4>
                  <p className="text-body-card text-muted-foreground">
                    {t(`pricing.faqs.${key}.desc`)}
                  </p>
                </motion.div>
              ))}
            </motion.div>
            <div className="mt-12 text-center">
              <Link to="/#contact">
                <Button
                  size="lg"
                  className="group h-12 gap-2 rounded-xl bg-primary px-8 text-sm font-semibold text-primary-foreground shadow-brand transition-all duration-250 ease-smooth hover:bg-primary/90 hover:shadow-elevated hover:-translate-y-0.5"
                >
                  {t("pricing.contactSales")}
                  <ArrowIcon
                    size={16}
                    className="transition-transform duration-250 ease-smooth group-hover:translate-x-0.5"
                  />
                </Button>
              </Link>
            </div>
          </motion.div>
        </div>
      </section>

      <Footer />
    </div>
  );
};

export default Pricing;
