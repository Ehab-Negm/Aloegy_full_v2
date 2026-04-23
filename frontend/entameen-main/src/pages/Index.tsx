import { useEffect, useRef, useState } from "react";
import { motion, useInView, useMotionValue, useSpring, useTransform } from "framer-motion";
import { Mic, PhoneCall, PhoneIncoming, TrendingUp, Utensils, Zap, ArrowLeft, BarChart3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import VoiceAssistantWidget from "@/components/VoiceAssistantWidget";
import { Link } from "react-router-dom";
import logo from "@/assets/logo.png";
import mascot from "@/assets/aloegy-mascot.png";
import { submitContactForm } from "@/services/api";

const services = [
  {
    icon: PhoneCall,
    title: "وكيل صوتي ابن بلد",
    description: "بيرد على كل تليفون بمصري صميم وبيأخد الأوردر زي ما الزبون عايز بالظبط.",
  },
  {
    icon: PhoneIncoming,
    title: "مفيش مكالمة بتضيع",
    description: "لو ١٠٠ واحد اتصلوا في نفس الثانية، كلهم هيترد عليهم. مفيش خط مشغول تاني.",
  },
  {
    icon: TrendingUp,
    title: "بيزوّد مبيعاتك",
    description: "بيقترح إضافات ذكية للزبون وبيزوّد الفاتورة ٢٠٪ أو أكتر من غير ما الزبون يحس.",
  },
  {
    icon: Utensils,
    title: "متوصّل بالكاشير",
    description: "الأوردر بينزل على الكاشير والمطبخ أوتوماتيك. بنشتغل مع فودكس وغيرها.",
  },
  {
    icon: BarChart3,
    title: "بتعرف كل حاجة",
    description: "تقارير عن كل مكالمة وكل أوردر. تعرف إيه اللي ماشي وإيه اللي محتاج يتظبط.",
  },
  {
    icon: Zap,
    title: "شغال ٢٤ ساعة",
    description: "مش بيعيا ومش بيأخد إجازة. شغال الفجر والضهر وبليل. ردود فورية دايماً.",
  },
];

const advantages = [
  { value: "٢٠٪+", counter: { to: 20, suffix: "٪+" }, label: "زيادة في المبيعات", desc: "من الإضافات الذكية اللي بيقترحها" },
  { value: "٠", counter: { to: 0, suffix: "" }, label: "مكالمة ضايعة", desc: "كل تليفون بيترد عليه" },
  { value: "∞", counter: null, label: "مكالمة في وقت واحد", desc: "مفيش حدود خالص" },
  { value: "٢٤/٧", counter: null, label: "شغال من غير ما يقفل", desc: "مفيش إجازات ولا أوفر تايم" },
];

const fadeUp = {
  hidden: { opacity: 0, y: 30 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.1, duration: 0.5, ease: "easeOut" as const },
  }),
};

// Convert Western digits to Arabic-Indic for UI display.
const AR_DIGIT_MAP = ["٠","١","٢","٣","٤","٥","٦","٧","٨","٩"];
const toArabicDigits = (n: number): string =>
  String(Math.round(n))
    .split("")
    .map((c) => (c >= "0" && c <= "9" ? AR_DIGIT_MAP[+c] : c))
    .join("");

// Counter that animates 0 → `to` when the node scrolls into view.
const AnimatedCounter = ({ to, prefix = "", suffix = "" }: { to: number; prefix?: string; suffix?: string }) => {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  const value = useMotionValue(0);
  const spring = useSpring(value, { duration: 1.6, bounce: 0 });
  const display = useTransform(spring, (v) => `${prefix}${toArabicDigits(v)}${suffix}`);
  useEffect(() => {
    if (inView) value.set(to);
  }, [inView, to, value]);
  return <motion.span ref={ref}>{display}</motion.span>;
};

// The mascot character with a layered in-place idle animation (float + sway
// + tilt + breathing scale), concentric voice-wave rings, and subtle
// mouse-parallax.  The PNG has a transparent background so no blend tricks
// are needed.
const HeroMascot = () => {
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  // Parallax tilt follows the mouse; spring-smoothed so motion feels organic.
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
      {/* Voice wave rings — 3 concentric pulses at staggered delays. */}
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

      {/* Soft primary-colour glow behind the mascot. */}
      <motion.div
        className="absolute w-[300px] h-[300px] rounded-full bg-primary/20 blur-[70px] pointer-events-none"
        animate={{ opacity: [0.6, 0.9, 0.6] }}
        transition={{ duration: 2.6, repeat: Infinity, ease: "easeInOut" }}
      />

      {/* Parallax layer: follows the mouse with a gentle spring. */}
      <motion.div
        style={{ x: parallaxX, y: parallaxY }}
        className="relative z-10"
      >
        {/* Float + sway + head-tilt layer: idle-in-place motion. Each axis
           has its own duration so the combined motion never loops exactly,
           which reads as "alive" instead of "looping". */}
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
          {/* Breathing scale — short/subtle, inner wrapper so it doesn't
             interfere with the sway rotation. */}
          <motion.div
            animate={{ scale: [1, 1.035, 1] }}
            transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
          >
            <img
              src={mascot}
              alt="ألو إيچي"
              className="w-[180px] md:w-[230px] drop-shadow-[0_20px_40px_rgba(59,130,246,0.4)] select-none"
              draggable={false}
            />
          </motion.div>
        </motion.div>
      </motion.div>
    </div>
  );
};

const Index = () => {
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
                الذكاء الاصطناعي المتربي في مصر
              </motion.span>

              <h1 className="text-5xl md:text-7xl font-heading font-extrabold text-foreground leading-tight mb-4">
                ألو إيچي
              </h1>

              <p className="text-2xl md:text-3xl font-heading font-semibold text-foreground/75 mb-2">
                أسرع ألو في مصر
              </p>
              <p className="text-lg text-muted-foreground mb-3">
                ذكاء اصطناعي بلسان مصري
              </p>
              <p className="text-base text-muted-foreground mb-8 max-w-xl md:mx-0 mx-auto leading-relaxed">
                وكيل صوتي ابن بلد بيتكلم مصري زيك بالظبط، بيأخد أوردرات المطاعم بدل الموظف، وبيزوّد مبيعاتك ٢٠٪ من الإضافات الذكية. مفيش تليفون هيرن ومحدش يرد عليه تاني.
              </p>
              <div className="flex flex-col sm:flex-row gap-3 md:justify-start justify-center">
                <Button
                  size="lg"
                  onClick={() => window.dispatchEvent(new Event("aloegy:open-voice"))}
                  className="rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand px-8 gap-2 h-12"
                >
                  <Mic size={18} />
                  جرب صوت ألو إيچي
                </Button>
                <a href="#services">
                  <Button size="lg" variant="outline" className="rounded-lg border-border px-8 h-12 hover:bg-muted/50 gap-2 w-full sm:w-auto">
                    اعرف أكتر
                    <ArrowLeft size={18} />
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

      {/* Advantages Stats */}
      <section className="py-16 border-y border-border/50 bg-card">
        <div className="container mx-auto px-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
            {advantages.map((item, i) => (
              <motion.div
                key={item.label}
                custom={i}
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUp}
                className="text-center"
              >
                <div className="text-4xl md:text-5xl font-heading font-extrabold text-primary mb-1">
                  {item.counter
                    ? <AnimatedCounter to={item.counter.to} suffix={item.counter.suffix} />
                    : item.value}
                </div>
                <div className="font-heading font-semibold text-foreground text-sm mb-0.5">{item.label}</div>
                <div className="text-xs text-muted-foreground">{item.desc}</div>
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
              إيه اللي بنعمله؟
            </h2>
            <p className="text-muted-foreground max-w-md mx-auto">
              كل اللي مطعمك محتاجه عشان مفيش تليفون يضيع ومبيعاتك تزيد
            </p>
          </motion.div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {services.map((service, i) => (
              <motion.div
                key={service.title}
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
                    <h3 className="font-heading font-semibold text-lg mb-2 text-foreground">{service.title}</h3>
                    <p className="text-sm text-muted-foreground leading-relaxed">{service.description}</p>
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
                مين إحنا؟
              </h2>
              <p className="text-muted-foreground leading-relaxed mb-4">
                ألو إيچي هي أول منصة ذكاء اصطناعي صوتي في مصر متخصصة في المطاعم. عملنا وكيل صوتي بيتكلم مصري ابن بلد زيك بالظبط، بيأخد الأوردرات من الزباين على التليفون بدل الموظف.
              </p>
              <p className="text-muted-foreground leading-relaxed mb-4">
                المشكلة بسيطة: مطعمك بيضيع مكالمات كتير وقت الزحمة لإن الخط مشغول. إحنا بنرد على عدد لا نهائي من التليفونات في نفس اللحظة، وكمان بنقترح إضافات ذكية بتزوّد الفاتورة ٢٠٪ أو أكتر.
              </p>
              <p className="text-muted-foreground leading-relaxed mb-8">
                متوصّلين بأشهر أنظمة الكاشير في مصر عشان الأوردر ينزل المطبخ لوحده من غير تدخل بشري. فريقنا كله مهندسين مصريين بيبنوا التكنولوجيا دي عشان تمشي مع السوق المصري.
              </p>
              <div className="flex gap-10">
                <div>
                  <div className="text-3xl font-heading font-extrabold text-primary">
                    <AnimatedCounter prefix="+" to={200} />
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">مطعم معانا</div>
                </div>
                <div>
                  <div className="text-3xl font-heading font-extrabold text-primary">
                    <AnimatedCounter prefix="+" to={50} suffix=" ألف" />
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">مكالمة اتأخدت</div>
                </div>
                <div>
                  <div className="text-3xl font-heading font-extrabold text-primary">
                    <AnimatedCounter to={20} suffix="٪+" />
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">زيادة مبيعات</div>
                </div>
              </div>
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
                      alt="ألو إيچي"
                      className="w-28 h-28 mx-auto mb-5 object-contain drop-shadow-md"
                      animate={{ rotate: [0, 3, -3, 0] }}
                      transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
                    />
                    <p className="font-heading font-bold text-xl text-foreground">ألو إيچي</p>
                    <p className="text-sm text-muted-foreground mt-1">أسرع ألو في مصر</p>
                    <p className="text-xs text-muted-foreground mt-0.5">الذكاء الاصطناعي المتربي في مصر</p>
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
              إزاي بيشتغل؟
            </h2>
          </motion.div>

          <div className="grid md:grid-cols-3 gap-10 max-w-4xl mx-auto">
            {[
              { step: "١", title: "الزبون بيتصل", desc: "الزبون بيكلم نمرة المطعم زي ما هو معوّد عادي" },
              { step: "٢", title: "ألو إيچي بيرد", desc: "الوكيل الذكي بيرد بمصري ابن بلد وبيأخد الأوردر ويقترح إضافات" },
              { step: "٣", title: "الأوردر بينزل المطبخ", desc: "الأوردر بينزل الكاشير والمطبخ أوتوماتيك في ثانية" },
            ].map((item, i) => (
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
                <h3 className="font-heading font-semibold text-lg mb-2 text-foreground">{item.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{item.desc}</p>
              </motion.div>
            ))}
          </div>

          {/* CTA */}
          <div className="text-center mt-14">
            <Link to="/pricing">
              <Button size="lg" className="rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand px-10 gap-2 text-lg h-13">
                ابدأ دلوقتي
                <ArrowLeft size={20} />
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
              كلمنا دلوقتي
            </h2>
            <p className="text-muted-foreground mb-8">
              عايز تشوف ألو إيچي في مطعمك؟ ابعتلنا وهنكلمك في أسرع وقت.
            </p>
            <div className="bg-background rounded-2xl p-8 shadow-elevated border border-border/60">
              {submitted ? (
                <div className="text-center py-8">
                  <div className="w-14 h-14 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
                    <svg className="w-7 h-7 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                  </div>
                  <p className="text-lg font-heading font-semibold text-foreground mb-2">تمام! وصلتنا رسالتك</p>
                  <p className="text-sm text-muted-foreground">هنكلمك في أقرب وقت إن شاء الله</p>
                </div>
              ) : (
                <div className="space-y-4">
                  <input
                    value={contactForm.restaurantName}
                    onChange={(e) => setContactForm((p) => ({ ...p, restaurantName: e.target.value }))}
                    placeholder="اسم المطعم"
                    className="w-full px-4 py-3 rounded-lg bg-muted/50 border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30 transition-all"
                  />
                  <input
                    value={contactForm.phone}
                    onChange={(e) => setContactForm((p) => ({ ...p, phone: e.target.value }))}
                    placeholder="رقم الموبايل"
                    className="w-full px-4 py-3 rounded-lg bg-muted/50 border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30 transition-all"
                    dir="ltr"
                  />
                  <textarea
                    value={contactForm.message}
                    onChange={(e) => setContactForm((p) => ({ ...p, message: e.target.value }))}
                    placeholder="عايز تقولنا إيه..."
                    rows={4}
                    className="w-full px-4 py-3 rounded-lg bg-muted/50 border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30 transition-all resize-none"
                  />
                  <Button
                    onClick={handleContactSubmit}
                    disabled={submitting || !contactForm.restaurantName || !contactForm.phone}
                    className="w-full rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand h-12"
                  >
                    {submitting ? "جاري الإرسال..." : "ابعت الرسالة"}
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
