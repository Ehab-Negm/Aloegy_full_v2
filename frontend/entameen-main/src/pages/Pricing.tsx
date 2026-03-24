import { motion } from "framer-motion";
import { Check, ArrowLeft, PhoneCall, MessageSquare, Headphones } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import { Link } from "react-router-dom";

const plans = [
  {
    name: "الأساسية",
    icon: PhoneCall,
    description: "ابدأ مع وكيل ذكي شغال ليل ونهار يأخد أوردراتك",
    price: "٤,٠٠٠",
    oldPrice: "٦,٠٠٠",
    period: "شهرياً",
    popular: false,
    features: [
      "وكيل صوتي ذكي بلهجة مصرية",
      "شغال ٢٤ ساعة / ٧ أيام",
      "مكالمة واحدة في نفس الوقت",
      "اقتراح إضافات ذكية لزيادة المبيعات",
      "ربط مع نظام الكاشير",
      "تقارير يومية عن المكالمات",
      "دعم فني خلال ساعات العمل",
    ],
  },
  {
    name: "المتقدمة",
    icon: Headphones,
    description: "للمطاعم اللي عندها ضغط مكالمات وعايزة تكبر مبيعاتها",
    price: "٦,٠٠٠",
    oldPrice: "٨,٠٠٠",
    period: "شهرياً",
    popular: true,
    features: [
      "كل مميزات الباقة الأساسية",
      "لحد ٥ مكالمات في نفس الوقت",
      "تحليلات متقدمة وتقارير أسبوعية",
      "تخصيص ردود الوكيل حسب المنيو",
      "اقتراحات ذكية مخصصة لكل زبون",
      "أولوية في الدعم الفني",
      "تحديثات دورية مجانية",
    ],
  },
  {
    name: "الاحترافية",
    icon: MessageSquare,
    description: "الحل الكامل للمطاعم الكبيرة والسلاسل اللي عايزة تسيطر على كل القنوات",
    price: "تواصل معانا",
    oldPrice: null,
    period: "",
    popular: false,
    features: [
      "كل مميزات الباقة المتقدمة",
      "لحد ١٢ مكالمة في نفس الوقت",
      "رد على الواتساب مجاني",
      "أخد أوردرات من واتساب",
      "مكالمات على رقم المطعم مباشرة",
      "مدير حساب مخصص",
      "ربط مع أكتر من فرع",
      "تقارير وتحليلات بالذكاء الاصطناعي",
    ],
  },
];

const fadeUp = {
  hidden: { opacity: 0, y: 30 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.15, duration: 0.5, ease: "easeOut" as const },
  }),
};

const Pricing = () => {
  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      <section className="relative pt-32 pb-20 overflow-hidden">
        <div className="absolute inset-0 pointer-events-none gradient-hero" />

        <div className="container mx-auto px-4 relative">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="text-center mb-16"
          >
            <h1 className="text-3xl md:text-5xl font-heading font-bold mb-4 text-foreground">
              اختار الباقة المناسبة لمطعمك
            </h1>
            <p className="text-muted-foreground max-w-lg mx-auto leading-relaxed">
              كل الباقات فيها وكيل صوتي ذكي بيتكلم مصري وبيأخد الأوردرات بدل الموظف. الفرق في عدد المكالمات المتزامنة والمميزات الإضافية.
            </p>
          </motion.div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-5 max-w-5xl mx-auto">
            {plans.map((plan, i) => (
              <motion.div
                key={plan.name}
                custom={i}
                initial="hidden"
                animate="visible"
                variants={fadeUp}
                className="relative"
              >
                {plan.popular && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2 z-10">
                    <Badge className="bg-primary text-primary-foreground border-0 px-4 py-1 text-xs font-medium shadow-brand">
                      الأكتر طلباً
                    </Badge>
                  </div>
                )}
                <Card className={`h-full border-border/60 bg-card transition-all duration-300 hover:shadow-elevated ${
                  plan.popular ? "border-primary/30 shadow-brand ring-1 ring-primary/10" : ""
                }`}>
                  <CardHeader className="pb-2 pt-8 text-center">
                    <div className={`w-12 h-12 rounded-lg flex items-center justify-center mx-auto mb-4 ${
                      plan.popular ? "bg-primary shadow-brand" : "bg-primary/10"
                    }`}>
                      <plan.icon size={24} className={plan.popular ? "text-primary-foreground" : "text-primary"} />
                    </div>
                    <h3 className="font-heading font-bold text-xl text-foreground">{plan.name}</h3>
                    <p className="text-sm text-muted-foreground mt-1 min-h-[40px]">{plan.description}</p>
                  </CardHeader>
                  <CardContent className="pt-4">
                    <div className="text-center mb-6">
                      {plan.oldPrice && (
                        <p className="text-sm text-muted-foreground line-through mb-1">{plan.oldPrice} ج.م</p>
                      )}
                      <div className="flex items-baseline justify-center gap-1">
                        <span className="text-4xl font-heading font-extrabold text-foreground">{plan.price}</span>
                        {plan.period && <span className="text-sm text-muted-foreground">ج.م / {plan.period}</span>}
                      </div>
                    </div>

                    <ul className="space-y-3 mb-8">
                      {plan.features.map((feature) => (
                        <li key={feature} className="flex items-start gap-2 text-sm">
                          <Check size={15} className="text-primary mt-0.5 shrink-0" />
                          <span className="text-muted-foreground">{feature}</span>
                        </li>
                      ))}
                    </ul>

                    <Link to="/#contact">
                      <Button className={`w-full rounded-lg h-11 ${
                        plan.popular
                          ? "bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand"
                          : "bg-secondary text-secondary-foreground hover:bg-secondary/80"
                      }`}>
                        {plan.price === "تواصل معانا" ? "كلمنا دلوقتي" : "اشترك دلوقتي"}
                      </Button>
                    </Link>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </div>

          {/* FAQ section */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.6, duration: 0.5 }}
            className="max-w-3xl mx-auto mt-24 text-center"
          >
            <h2 className="text-2xl font-heading font-bold mb-8 text-foreground">
              ليه مطعمك محتاج ألو إيچي؟
            </h2>
            <div className="grid md:grid-cols-2 gap-5 text-start">
              {[
                { title: "مبيعات أكتر من غير مجهود", desc: "الوكيل الذكي بيقترح إضافات لكل زبون بطريقة طبيعية. النتيجة؟ زيادة في الفاتورة من ٢٠٪ لحد ٩٠٪ في بعض الأوردرات." },
                { title: "مفيش تليفون بيضيع تاني", desc: "في أوقات الذروة، الخط مشغول والزبون بيروح لمطعم تاني. مع ألو إيچي، كل المكالمات بيترد عليها في نفس اللحظة." },
                { title: "وفّر في المرتبات", desc: "موظف التليفونات بيكلّفك ٥,٠٠٠ جنيه أو أكتر في الشهر، وبيشتغل ٨ ساعات بس. ألو إيچي شغال ٢٤ ساعة بتكلفة أقل وأداء أعلى." },
                { title: "بيتكلم مصري صميم", desc: "مش روبوت بيتكلم فصحى. ده وكيل ذكي بيتكلم مصري ابن بلد، الزبون مش هيفرق إنه بيكلم ذكاء اصطناعي." },
              ].map((item) => (
                <Card key={item.title} className="border-border/60 bg-card">
                  <CardContent className="p-6">
                    <h4 className="font-heading font-semibold text-foreground mb-2">{item.title}</h4>
                    <p className="text-sm text-muted-foreground leading-relaxed">{item.desc}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </motion.div>
        </div>
      </section>

      <Footer />
    </div>
  );
};

export default Pricing;
