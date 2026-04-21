import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Bot, CheckCircle2, Clock, MapPin, Phone as PhoneIcon, Plus, Save, Store, Trash2 } from "lucide-react";

import { saveSettings, fetchSettings, type BranchSettings, type SettingsResponse } from "@/services/api";
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

const createEmptyBranch = (): BranchSettings => ({
  name: "",
  address: "",
  deliveryZones: [],
});

const EMPTY_STATE: SettingsResponse = {
  restaurant: {
    name: "",
    address: "",
    workingHours: "",
    contactPhone: "",
    branches: [createEmptyBranch()],
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

const splitZones = (value: string) =>
  value
    .split(/[,،\n]/)
    .map((zone) => zone.trim())
    .filter(Boolean);

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
        setRestaurant({
          ...data.restaurant,
          branches: data.restaurant.branches?.length ? data.restaurant.branches : [createEmptyBranch()],
        });
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

  const readinessChecks = useMemo(
    () => [
      {
        label: "بيانات المطعم الأساسية",
        ok: Boolean(restaurant.name.trim() && restaurant.address.trim()),
      },
      {
        label: "رقم تواصل صالح",
        ok: Boolean(restaurant.contactPhone.trim()),
      },
      {
        label: "ساعات العمل مكتوبة",
        ok: Boolean(restaurant.workingHours.trim()),
      },
      {
        label: "فرع واحد على الأقل جاهز",
        ok: restaurant.branches.some((branch) => branch.name.trim() && branch.address.trim()),
      },
    ],
    [restaurant],
  );

  const readyCount = readinessChecks.filter((item) => item.ok).length;

  const updateBranch = (index: number, field: keyof BranchSettings, value: string | string[]) => {
    setRestaurant((current) => {
      const nextBranches = current.branches.map((branch, branchIndex) =>
        branchIndex === index ? { ...branch, [field]: value } : branch,
      );
      const nextRestaurant = { ...current, branches: nextBranches };
      if (index === 0 && field === "address" && typeof value === "string") {
        nextRestaurant.address = value;
      }
      return nextRestaurant;
    });
  };

  const addBranch = () => {
    setRestaurant((current) => ({
      ...current,
      branches: [...current.branches, createEmptyBranch()],
    }));
  };

  const removeBranch = (index: number) => {
    setRestaurant((current) => {
      const nextBranches = current.branches.filter((_, branchIndex) => branchIndex !== index);
      return {
        ...current,
        branches: nextBranches.length ? nextBranches : [createEmptyBranch()],
      };
    });
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      const payload: SettingsResponse = {
        restaurant: {
          ...restaurant,
          branches: restaurant.branches
            .map((branch) => ({
              ...branch,
              name: branch.name.trim(),
              address: branch.address.trim(),
              deliveryZones: branch.deliveryZones.map((zone) => zone.trim()).filter(Boolean),
            }))
            .filter((branch) => branch.name || branch.address || branch.deliveryZones.length),
        },
        agent,
      };
      const saved = await saveSettings(payload, restaurantId);
      setRestaurant({
        ...saved.restaurant,
        branches: saved.restaurant.branches?.length ? saved.restaurant.branches : [createEmptyBranch()],
      });
      setAgent(saved.agent);
      toast({ title: "تم الحفظ ✅", description: "الإعدادات اتحدثت وبقت جاهزة للتشغيل" });
    } catch (error) {
      console.error("Failed to save settings:", error);
      toast({ title: "خطأ", description: "الحفظ فشل، حاول تاني", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const disabled = Boolean(readOnly || loading || saving);

  return (
    <div className="space-y-6">
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <CheckCircle2 size={18} className="text-primary" />
            جاهزية التشغيل
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-4">
            {[
              { label: "Checklist", value: `${readyCount}/${readinessChecks.length}` },
              { label: "الفروع", value: String(restaurant.branches.length) },
              { label: "زونز التوصيل", value: String(restaurant.branches.reduce((sum, branch) => sum + branch.deliveryZones.length, 0)) },
              { label: "اسم الوكيل", value: agent.name.trim() || "—" },
            ].map((item) => (
              <div key={item.label} className="rounded-xl border border-border/50 bg-muted/20 p-4">
                <p className="text-lg font-heading font-bold text-foreground">{item.value}</p>
                <p className="text-xs text-muted-foreground">{item.label}</p>
              </div>
            ))}
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            {readinessChecks.map((item) => (
              <div
                key={item.label}
                className={`flex items-center gap-3 rounded-xl border px-4 py-3 ${
                  item.ok ? "border-emerald-200 bg-emerald-50/70" : "border-amber-200 bg-amber-50/70"
                }`}
              >
                {item.ok ? (
                  <CheckCircle2 size={16} className="text-emerald-600" />
                ) : (
                  <AlertTriangle size={16} className="text-amber-600" />
                )}
                <span className="text-sm text-foreground">{item.label}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

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
            <Label className="flex items-center gap-1 text-sm font-medium"><MapPin size={14} /> العنوان الرئيسي</Label>
            <Input
              value={restaurant.address}
              onChange={(e) => {
                const value = e.target.value;
                setRestaurant((current) => ({
                  ...current,
                  address: value,
                  branches: current.branches.map((branch, index) => (index === 0 ? { ...branch, address: value } : branch)),
                }));
              }}
              disabled={disabled}
              className="rounded-xl"
            />
          </div>
          <div className="space-y-2">
            <Label className="flex items-center gap-1 text-sm font-medium"><Clock size={14} /> ساعات العمل</Label>
            <Textarea value={restaurant.workingHours} onChange={(e) => setRestaurant({ ...restaurant, workingHours: e.target.value })} disabled={disabled} className="min-h-[60px] rounded-xl" />
            <p className="text-xs text-muted-foreground">اكتبها بشكل واضح زي: يوميًا من 10 صباحًا لـ 12 صباحًا</p>
          </div>
          <div className="space-y-2">
            <Label className="flex items-center gap-1 text-sm font-medium"><PhoneIcon size={14} /> رقم التواصل</Label>
            <Input value={restaurant.contactPhone} onChange={(e) => setRestaurant({ ...restaurant, contactPhone: e.target.value })} disabled={disabled} className="rounded-xl" dir="ltr" />
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/50">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <MapPin size={18} className="text-primary" />
            الفروع وdelivery zones
          </CardTitle>
          {!readOnly && (
            <Button onClick={addBranch} type="button" size="sm" className="gap-2 rounded-xl" disabled={disabled}>
              <Plus size={16} />
              أضف فرع
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          {restaurant.branches.map((branch, index) => (
            <div key={`branch-${index}`} className="rounded-2xl border border-border/50 bg-muted/20 p-4">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-foreground">فرع {index + 1}</p>
                  <p className="text-xs text-muted-foreground">الفرع ده هو اللي الـ agent هيستخدمه في routing والحجز والتوصيل.</p>
                </div>
                {!readOnly && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-destructive/70 hover:text-destructive"
                    onClick={() => removeBranch(index)}
                    disabled={disabled || restaurant.branches.length === 1}
                  >
                    <Trash2 size={14} />
                  </Button>
                )}
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label className="text-sm font-medium">اسم الفرع</Label>
                  <Input
                    value={branch.name}
                    onChange={(e) => updateBranch(index, "name", e.target.value)}
                    disabled={disabled}
                    className="rounded-xl"
                    placeholder="مثال: فرع الدقي"
                  />
                </div>
                <div className="space-y-2">
                  <Label className="text-sm font-medium">عنوان الفرع</Label>
                  <Input
                    value={branch.address}
                    onChange={(e) => updateBranch(index, "address", e.target.value)}
                    disabled={disabled}
                    className="rounded-xl"
                    placeholder="مثال: 12 شارع التحرير، الدقي"
                  />
                </div>
              </div>

              <div className="mt-4 space-y-2">
                <Label className="text-sm font-medium">مناطق التوصيل</Label>
                <Input
                  value={branch.deliveryZones.join("، ")}
                  onChange={(e) => updateBranch(index, "deliveryZones", splitZones(e.target.value))}
                  disabled={disabled}
                  className="rounded-xl"
                  placeholder="مثال: الدقي، المهندسين، الزمالك"
                />
                <p className="text-xs text-muted-foreground">افصل بين المناطق بـ `،` أو `,` عشان الـ agent يعرف يلتقط الزون بسرعة.</p>
              </div>
            </div>
          ))}
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
