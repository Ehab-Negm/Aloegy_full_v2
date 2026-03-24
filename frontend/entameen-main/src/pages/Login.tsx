import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { ArrowLeft, ArrowRight, Phone } from "lucide-react";

import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import logo from "@/assets/logo.png";
import { getStoredRole, getStoredToken, loginWithPhone, storeAuthSession, verifyOtp, type UserRole } from "@/services/api";

const rolePath: Record<UserRole, string> = {
  admin: "/admin",
  sales: "/sales",
  owner: "/dashboard",
  employee: "/dashboard",
};

const Login = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [step, setStep] = useState<"phone" | "otp">("phone");
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const token = getStoredToken();
    const role = getStoredRole();
    if (token && role) {
      navigate(rolePath[role], { replace: true });
    }
  }, [navigate]);

  const handleSendOtp = async () => {
    if (phone.length < 10) {
      return;
    }

    setLoading(true);
    try {
      await loginWithPhone(phone);
      setStep("otp");
      toast({
        title: "تم الإرسال",
        description: "راجع كود التحقق على رقمك",
      });
    } catch (error) {
      console.error("Failed to send OTP:", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نبعت الكود دلوقتي",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async () => {
    if (otp.length < 6) {
      return;
    }

    setLoading(true);
    try {
      const result = await verifyOtp(phone, otp);
      if (!result.success || !result.token || !result.role) {
        throw new Error("بيانات الدخول ناقصة من السيرفر");
      }

      storeAuthSession(result.token, phone, result.role);
      navigate(rolePath[result.role], { replace: true });
    } catch (error) {
      console.error("Failed to verify OTP:", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "الكود غير صحيح أو منتهي",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="pointer-events-none absolute inset-0 gradient-hero" />

      <Link to="/" className="absolute start-6 top-6 z-10">
        <Button variant="ghost" size="sm" className="gap-1 text-muted-foreground hover:text-foreground">
          الرئيسية
          <ArrowRight size={16} />
        </Button>
      </Link>

      <motion.div
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="relative w-full max-w-md"
      >
        <div className="mb-8 text-center">
          <Link to="/" className="inline-flex items-center gap-2.5">
            <img src={logo} alt="ألو إيچي" className="h-11 w-11 object-contain" />
            <h1 className="text-3xl font-heading font-bold text-foreground">ألو إيچي</h1>
          </Link>
          <p className="mt-2 text-sm text-muted-foreground">ادخل على حسابك</p>
        </div>

        <div className="rounded-2xl border border-border/60 bg-card p-8 shadow-elevated">
          {step === "phone" ? (
            <div className="space-y-6">
              <div className="text-center">
                <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
                  <Phone size={26} className="text-primary" />
                </div>
                <h2 className="font-heading text-lg font-semibold text-foreground">دخّل رقم موبايلك</h2>
                <p className="mt-1 text-sm text-muted-foreground">هنبعتلك كود تحقق على الرقم ده</p>
              </div>

              <div>
                <input
                  type="tel"
                  value={phone}
                  onChange={(event) => setPhone(event.target.value)}
                  placeholder="+20 1XX XXX XXXX"
                  dir="ltr"
                  className="w-full rounded-lg border border-border bg-muted/50 px-4 py-3 text-center text-lg tracking-wider transition-all focus:border-primary/30 focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              <Button
                onClick={() => void handleSendOtp()}
                disabled={phone.length < 10 || loading}
                className="h-12 w-full rounded-lg bg-primary text-base text-primary-foreground hover:bg-primary/90 shadow-brand"
              >
                {loading ? "جاري الإرسال..." : "ابعت كود التحقق"}
              </Button>
            </div>
          ) : (
            <div className="space-y-6">
              <div className="text-center">
                <h2 className="font-heading text-lg font-semibold text-foreground">دخّل كود التحقق</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  بعتنا الكود على <span dir="ltr" className="font-medium text-primary">{phone}</span>
                </p>
              </div>

              <div className="flex justify-center" dir="ltr">
                <InputOTP maxLength={6} value={otp} onChange={setOtp}>
                  <InputOTPGroup>
                    <InputOTPSlot index={0} />
                    <InputOTPSlot index={1} />
                    <InputOTPSlot index={2} />
                    <InputOTPSlot index={3} />
                    <InputOTPSlot index={4} />
                    <InputOTPSlot index={5} />
                  </InputOTPGroup>
                </InputOTP>
              </div>

              <Button
                onClick={() => void handleVerifyOtp()}
                disabled={otp.length < 6 || loading}
                className="h-12 w-full rounded-lg bg-primary text-base text-primary-foreground hover:bg-primary/90 shadow-brand"
              >
                {loading ? "جاري التحقق..." : "ادخل"}
              </Button>

              <button
                onClick={() => {
                  setStep("phone");
                  setOtp("");
                }}
                className="flex w-full items-center justify-center gap-1 text-sm text-muted-foreground transition-colors hover:text-primary"
              >
                <ArrowLeft size={14} />
                غيّر رقم الموبايل
              </button>
            </div>
          )}
        </div>
      </motion.div>
    </div>
  );
};

export default Login;
