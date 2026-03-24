import { Phone, Mail, MapPin } from "lucide-react";
import { Link } from "react-router-dom";
import logo from "@/assets/logo.png";

const Footer = () => {
  return (
    <footer className="bg-foreground text-background">
      <div className="container mx-auto px-4 py-14">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-10">
          <div>
            <div className="flex items-center gap-2.5 mb-4">
              <img src={logo} alt="ألو إيچي" className="w-8 h-8 object-contain" />
              <h3 className="text-xl font-heading font-bold">ألو إيچي</h3>
            </div>
            <p className="text-sm font-medium mb-1 text-background/80">أسرع ألو في مصر</p>
            <p className="text-background/60 text-sm leading-relaxed">
              وكيل صوتي بالذكاء الاصطناعي بلهجة مصرية بيأخد أوردرات المطاعم ويزود مبيعاتك. الذكاء الاصطناعي المتربي في مصر.
            </p>
          </div>

          <div>
            <h4 className="font-heading font-semibold mb-4">لينكات سريعة</h4>
            <ul className="space-y-2.5 text-sm text-background/60">
              <li><a href="/" className="hover:text-background transition-colors">الرئيسية</a></li>
              <li><a href="/#services" className="hover:text-background transition-colors">خدماتنا</a></li>
              <li><Link to="/pricing" className="hover:text-background transition-colors">الأسعار</Link></li>
              <li><a href="/#about" className="hover:text-background transition-colors">مين إحنا</a></li>
              <li><a href="/#contact" className="hover:text-background transition-colors">كلمنا</a></li>
            </ul>
          </div>

          <div>
            <h4 className="font-heading font-semibold mb-4">كلمنا</h4>
            <div className="space-y-3 text-sm text-background/60">
              <div className="flex items-center gap-2.5">
                <Phone size={15} className="text-brand-glow" />
                <span dir="ltr">+20 123 456 7890</span>
              </div>
              <div className="flex items-center gap-2.5">
                <Mail size={15} className="text-brand-glow" />
                <span>contact@aloegy.ai</span>
              </div>
              <div className="flex items-center gap-2.5">
                <MapPin size={15} className="text-brand-glow" />
                <span>مصر</span>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-10 pt-6 border-t border-background/10 text-center text-sm text-background/40">
          <p>&copy; {new Date().getFullYear()} ألو إيچي. كل الحقوق محفوظة.</p>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
