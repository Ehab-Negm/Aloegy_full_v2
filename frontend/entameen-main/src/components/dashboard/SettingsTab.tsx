import { useEffect, useState } from "react";
import { Bot, Clock, MapPin, Phone as PhoneIcon, Save, Store } from "lucide-react";

import { saveSettings, fetchSettings } from "@/services/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/hooks/use-toast";

interface SettingsTabProps {
  readOnly?: boolean;
  restaurantId?: number;
}

const EMPTY_STATE = {
  restaurant: {
    name: "",
    address: "",
    workingHours: "",
    contactPhone: "",
  },
  agent: {
    name: "",
    voiceStyle: "",
    language: "",
    personality: "",
    preCallInstructions: "",
    supplementaryInfo: "",
  },
};

const SettingsTab = ({ readOnly, restaurantId }: SettingsTabProps) => {
  const { toast } = useToast();
  const [restaurant, setRestaurant] = useState(EMPTY_STATE.restaurant);
  const [agent, setAgent] = useState(EMPTY_STATE.agent);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let ignore = false;

    const load = async () => {
      try {
        setLoading(true);
        const data = await fetchSettings(restaurantId);
        if (ignore) return;
        setRestaurant(data.restaurant);
        setAgent(data.agent);
      } catch (error) {
        console.error("Failed to load settings:", error);
        if (!ignore) {
          toast({ title: "خطأ", description: "مش قادرين نجيب الإعدادات دلوقتي", variant: "destructive" });
        }
      } finally {
        if (!ignore) setLoading(false);
      }
    };

    void load();
    return () => {
      ignore = true;
    };
  }, [restaurantId, toast]);

  const handleSave = async () => {
    try {
      setSaving(true);
      await saveSettings({ restaurant, agent }, restaurantId);
      toast({ title: "تم الحفظ ✅", description: "الإعدادات اتحدثت بنجاح" });
    } catch (error) {
      console.error("Failed to save settings:", error);
      toast({ title: "خطأ", description: "الحفظ فشل، حاول تاني", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const disabled = Boolean(readOnly || loading || saving);

  return (
    <div className="max-w-2xl space-y-6">
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Store size={18} className="text-primary" />
            بيانات المطعم
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label className="text-sm font-medium">اسم المطعم</Label>
            <Input value={restaurant.name} onChange={(e) => setRestaurant({ ...restaurant, name: e.target.value })} disabled={disabled} className="rounded-xl" />
          </div>
          <div className="space-y-2">
            <Label className="flex items-center gap-1 text-sm font-medium"><MapPin size={14} /> العنوان</Label>
            <Input value={restaurant.address} onChange={(e) => setRestaurant({ ...restaurant, address: e.target.value })} disabled={disabled} className="rounded-xl" />
          </div>
          <div className="space-y-2">
            <Label className="flex items-center gap-1 text-sm font-medium"><Clock size={14} /> ساعات العمل</Label>
            <Textarea value={restaurant.workingHours} onChange={(e) => setRestaurant({ ...restaurant, workingHours: e.target.value })} disabled={disabled} className="min-h-[60px] rounded-xl" />
          </div>
          <div className="space-y-2">
            <Label className="flex items-center gap-1 text-sm font-medium"><PhoneIcon size={14} /> رقم التواصل</Label>
            <Input value={restaurant.contactPhone} onChange={(e) => setRestaurant({ ...restaurant, contactPhone: e.target.value })} disabled={disabled} className="rounded-xl" dir="ltr" />
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Bot size={18} className="text-primary" />
            إعدادات الوكيل الصوتي
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-2">
              <Label className="text-sm font-medium">اسم الوكيل</Label>
              <Input value={agent.name} onChange={(e) => setAgent({ ...agent, name: e.target.value })} disabled={disabled} className="rounded-xl" />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">أسلوب الصوت</Label>
              <Input value={agent.voiceStyle} onChange={(e) => setAgent({ ...agent, voiceStyle: e.target.value })} disabled={disabled} className="rounded-xl" />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">اللغة</Label>
              <Input value={agent.language} onChange={(e) => setAgent({ ...agent, language: e.target.value })} disabled={disabled} className="rounded-xl" />
            </div>
          </div>

          <div className="space-y-2">
            <Label className="text-sm font-medium">شخصية الوكيل</Label>
            <Textarea value={agent.personality} onChange={(e) => setAgent({ ...agent, personality: e.target.value })} disabled={disabled} className="min-h-[100px] rounded-xl" />
            <p className="text-xs text-muted-foreground">وصف شخصية الوكيل وأسلوبه مع العملاء</p>
          </div>

          <div className="space-y-2">
            <Label className="text-sm font-medium">تعليمات ما قبل المكالمة</Label>
            <Textarea value={agent.preCallInstructions} onChange={(e) => setAgent({ ...agent, preCallInstructions: e.target.value })} disabled={disabled} className="min-h-[100px] rounded-xl" />
            <p className="text-xs text-muted-foreground">قواعد تشغيلية يتبعها الوكيل في كل مكالمة</p>
          </div>

          <div className="space-y-2">
            <Label className="text-sm font-medium">معلومات إضافية للوكيل</Label>
            <Textarea value={agent.supplementaryInfo} onChange={(e) => setAgent({ ...agent, supplementaryInfo: e.target.value })} disabled={disabled} className="min-h-[120px] rounded-xl" />
            <p className="text-xs text-muted-foreground">أسئلة شائعة، عروض، سياسات - أي معلومات تساعد الوكيل يجاوب العملاء</p>
          </div>
        </CardContent>
      </Card>

      {!readOnly && (
        <Button onClick={() => void handleSave()} className="w-full gap-2 rounded-xl bg-primary text-primary-foreground" disabled={loading || saving}>
          <Save size={16} />
          {saving ? "جاري الحفظ..." : "حفظ كل الإعدادات"}
        </Button>
      )}
    </div>
  );
};

export default SettingsTab;
