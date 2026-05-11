import { motion } from "framer-motion";
import { useTranslation } from "react-i18next";

import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import { useLanguageDirection } from "@/hooks/use-language-direction";

const easeSmooth = [0.16, 1, 0.3, 1] as const;

interface LegalPageProps {
  /** Top-level i18n namespace key for this legal document. */
  kind: "privacy" | "terms";
  /** How many numbered sections live under that namespace (section1..sectionN). */
  sectionCount: number;
}

const LegalPage = ({ kind, sectionCount }: LegalPageProps) => {
  useLanguageDirection();
  const { t } = useTranslation();

  const sections = Array.from({ length: sectionCount }, (_, i) => i + 1);

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      <section className="relative overflow-hidden gradient-hero border-b border-border/60 pt-28 pb-12 sm:pt-36 sm:pb-16">
        <div className="container relative mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: easeSmooth }}
            className="mx-auto max-w-3xl text-center"
          >
            <div className="eyebrow mb-3">Legal</div>
            <h1 className="text-section text-foreground mb-3">{t(`${kind}.title`)}</h1>
            <p className="font-mono text-sm text-muted-foreground">{t(`${kind}.lastUpdated`)}</p>
          </motion.div>
        </div>
      </section>

      <section className="py-16 sm:py-20">
        <div className="container mx-auto">
          <motion.article
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: easeSmooth, delay: 0.1 }}
            className="mx-auto max-w-2xl"
          >
            <p className="mb-12 text-base text-foreground/80 leading-relaxed">
              {t(`${kind}.intro`)}
            </p>

            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true, margin: "-60px" }}
              variants={{
                hidden: {},
                visible: { transition: { staggerChildren: 0.04 } },
              }}
              className="space-y-10"
            >
              {sections.map((n) => (
                <motion.div
                  key={n}
                  variants={{
                    hidden: { opacity: 0, y: 16 },
                    visible: {
                      opacity: 1,
                      y: 0,
                      transition: { duration: 0.5, ease: easeSmooth },
                    },
                  }}
                >
                  <h2 className="mb-3 text-xl font-bold tracking-tight text-foreground">
                    {t(`${kind}.section${n}Title`)}
                  </h2>
                  <p className="text-base text-muted-foreground leading-relaxed">
                    {t(`${kind}.section${n}`)}
                  </p>
                </motion.div>
              ))}
            </motion.div>
          </motion.article>
        </div>
      </section>

      <Footer />
    </div>
  );
};

export default LegalPage;
