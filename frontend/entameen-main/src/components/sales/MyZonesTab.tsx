import { useEffect, useMemo, useState } from "react";
import {
  Camera,
  ChevronLeft,
  ImagePlus,
  Loader2,
  MapPin,
  MessageSquare,
  Phone,
  Send,
  Store,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/hooks/use-toast";
import VisitPhotoImg from "@/components/sales/VisitPhotoImg";
import {
  fetchMyRestaurantReports,
  fetchMyZones,
  submitMyRestaurantReport,
  type MyZoneWithRestaurants,
  type RestaurantStatus,
  type VisitOutcome,
  type VisitReport,
  type ZoneRestaurant,
} from "@/services/api";

const STATUS_LABEL: Record<RestaurantStatus, string> = {
  pending: "لسه ما اتزرتش",
  in_progress: "في الشغل",
  won: "اتقفل",
  lost: "اترفض",
  follow_up: "محتاج متابعة",
};

const STATUS_COLOR: Record<RestaurantStatus, string> = {
  pending: "bg-amber-100 text-amber-700 border-amber-200",
  in_progress: "bg-blue-100 text-blue-700 border-blue-200",
  won: "bg-emerald-100 text-emerald-700 border-emerald-200",
  lost: "bg-destructive/10 text-destructive border-destructive/20",
  follow_up: "bg-purple-100 text-purple-700 border-purple-200",
};

const OUTCOME_LABEL: Record<VisitOutcome, string> = {
  visited: "زيارة عادية",
  interested: "مهتم",
  won: "وافق ووقّع",
  lost: "رفض",
  follow_up: "محتاج متابعة",
  not_open: "كان مقفول",
};

const OUTCOME_COLOR: Record<VisitOutcome, string> = {
  visited: "bg-muted text-foreground/80",
  interested: "bg-blue-50 text-blue-700",
  won: "bg-emerald-50 text-emerald-700",
  lost: "bg-rose-50 text-rose-700",
  follow_up: "bg-purple-50 text-purple-700",
  not_open: "bg-gray-100 text-gray-600",
};

const formatDateTimeAr = (iso: string): string => {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ar-EG", { dateStyle: "short", timeStyle: "short" });
};

const buildMapHref = (restaurant: ZoneRestaurant): string | null => {
  if (restaurant.mapUrl) return restaurant.mapUrl;
  if (restaurant.location)
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(restaurant.location)}`;
  return null;
};

interface FormState {
  contactName: string;
  contactRole: string;
  contactPhone: string;
  whatHappened: string;
  outcome: VisitOutcome;
  nextStep: string;
  photos: File[];
  useGps: boolean;
}

const EMPTY_FORM: FormState = {
  contactName: "",
  contactRole: "",
  contactPhone: "",
  whatHappened: "",
  outcome: "visited",
  nextStep: "",
  photos: [],
  useGps: false,
};

const MyZonesTab = () => {
  const { toast } = useToast();
  const [zones, setZones] = useState<MyZoneWithRestaurants[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeZoneId, setActiveZoneId] = useState<number | null>(null);
  const [activeRestaurant, setActiveRestaurant] = useState<ZoneRestaurant | null>(null);
  const [reports, setReports] = useState<VisitReport[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let ignore = false;
    const load = async () => {
      try {
        setLoading(true);
        const data = await fetchMyZones();
        if (!ignore) setZones(data);
      } catch (error) {
        console.error("Failed to load zones", error);
        if (!ignore) {
          toast({ title: "خطأ", description: "مش قادرين نجيب الزونز دلوقتي", variant: "destructive" });
        }
      } finally {
        if (!ignore) setLoading(false);
      }
    };
    void load();
    return () => {
      ignore = true;
    };
  }, [toast]);

  const activeZone = useMemo(
    () => zones.find((zone) => zone.id === activeZoneId) || null,
    [zones, activeZoneId],
  );

  const totals = useMemo(() => {
    let restaurants = 0;
    let won = 0;
    let pending = 0;
    let inProgress = 0;
    let followUp = 0;
    zones.forEach((zone) => {
      restaurants += zone.restaurants.length;
      zone.restaurants.forEach((restaurant) => {
        if (restaurant.status === "won") won++;
        else if (restaurant.status === "pending") pending++;
        else if (restaurant.status === "in_progress") inProgress++;
        else if (restaurant.status === "follow_up") followUp++;
      });
    });
    return { restaurants, won, pending, inProgress, followUp };
  }, [zones]);

  const openRestaurant = async (restaurant: ZoneRestaurant) => {
    setActiveRestaurant(restaurant);
    setForm(EMPTY_FORM);
    setReports([]);
    try {
      setReportsLoading(true);
      const data = await fetchMyRestaurantReports(restaurant.id);
      setReports(data);
    } catch (error) {
      console.error("Failed to load reports", error);
      toast({ title: "خطأ", description: "مش قادرين نجيب التقارير", variant: "destructive" });
    } finally {
      setReportsLoading(false);
    }
  };

  const closeRestaurant = () => {
    setActiveRestaurant(null);
    setReports([]);
    setForm(EMPTY_FORM);
  };

  const handlePhotosChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    if (files.length === 0) return;
    setForm((current) => {
      const merged = [...current.photos, ...files].slice(0, 6);
      return { ...current, photos: merged };
    });
    event.target.value = "";
  };

  const removePhoto = (index: number) => {
    setForm((current) => ({
      ...current,
      photos: current.photos.filter((_, i) => i !== index),
    }));
  };

  const captureGpsAndSubmit = async () => {
    if (!activeRestaurant) return;
    if (!form.whatHappened.trim() && form.photos.length === 0) {
      toast({
        title: "خطأ",
        description: "اكتب اللي حصل أو ضيف صورة على الأقل",
        variant: "destructive",
      });
      return;
    }
    let lat: number | null = null;
    let lng: number | null = null;
    if (form.useGps && navigator.geolocation) {
      try {
        const pos = await new Promise<GeolocationPosition>((resolve, reject) => {
          navigator.geolocation.getCurrentPosition(resolve, reject, {
            enableHighAccuracy: true,
            timeout: 6000,
            maximumAge: 30_000,
          });
        });
        lat = pos.coords.latitude;
        lng = pos.coords.longitude;
      } catch (error) {
        console.warn("GPS unavailable", error);
        toast({ title: "GPS مش متاح", description: "هنحفظ التقرير بدون موقع." });
      }
    }
    try {
      setSubmitting(true);
      const report = await submitMyRestaurantReport(activeRestaurant.id, {
        contactName: form.contactName,
        contactRole: form.contactRole,
        contactPhone: form.contactPhone,
        whatHappened: form.whatHappened,
        outcome: form.outcome,
        nextStep: form.nextStep,
        photos: form.photos,
        lat,
        lng,
      });
      setReports((current) => [report, ...current]);
      // Update local zone state so the card reflects new status / count.
      setZones((current) =>
        current.map((zone) =>
          zone.id === activeRestaurant.zoneId
            ? {
                ...zone,
                restaurants: zone.restaurants.map((entry) =>
                  entry.id === activeRestaurant.id
                    ? {
                        ...entry,
                        status: outcomeToStatus(form.outcome),
                        reportsCount: entry.reportsCount + 1,
                        lastReportPreview:
                          form.whatHappened.slice(0, 240) ||
                          (form.photos.length ? `${form.photos.length} صورة` : entry.lastReportPreview),
                        lastReportAt: report.createdAt,
                      }
                    : entry,
                ),
              }
            : zone,
        ),
      );
      setActiveRestaurant((current) =>
        current
          ? {
              ...current,
              status: outcomeToStatus(form.outcome),
              reportsCount: current.reportsCount + 1,
              lastReportPreview:
                form.whatHappened.slice(0, 240) ||
                (form.photos.length ? `${form.photos.length} صورة` : current.lastReportPreview),
              lastReportAt: report.createdAt,
            }
          : current,
      );
      setForm(EMPTY_FORM);
      toast({ title: "تمام", description: "اتسجلت الزيارة" });
    } catch (error) {
      console.error("Failed to submit report", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نسجل الزيارة",
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading && zones.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-border/50 bg-muted/20 p-8 text-sm text-muted-foreground">
        <Loader2 size={16} className="ml-2 animate-spin text-primary" />
        بنحمل زوناتك...
      </div>
    );
  }

  if (!loading && zones.length === 0) {
    return (
      <div className="rounded-xl border border-border/50 bg-muted/20 p-8 text-center text-sm text-muted-foreground">
        لسه مفيش زونز مخصصة ليك. اطلب من الأدمن يخصصلك زون.
      </div>
    );
  }

  // Restaurant detail view
  if (activeRestaurant) {
    return (
      <RestaurantDetail
        restaurant={activeRestaurant}
        reports={reports}
        reportsLoading={reportsLoading}
        form={form}
        setForm={setForm}
        submitting={submitting}
        onBack={closeRestaurant}
        onPhotosChange={handlePhotosChange}
        onRemovePhoto={removePhoto}
        onSubmit={() => void captureGpsAndSubmit()}
      />
    );
  }

  // Zone detail view
  if (activeZone) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" className="gap-1 rounded-lg" onClick={() => setActiveZoneId(null)}>
            <ChevronLeft size={14} />
            رجوع
          </Button>
          <h2 className="font-heading text-lg font-bold">{activeZone.name}</h2>
          <Badge
            variant="outline"
            className="border-transparent text-xs"
            style={{ backgroundColor: `${activeZone.color}20`, color: activeZone.color }}
          >
            {activeZone.restaurantCount} مطعم
          </Badge>
        </div>

        {activeZone.description ? (
          <p className="rounded-lg bg-muted/40 p-3 text-sm text-foreground/80">{activeZone.description}</p>
        ) : null}

        {activeZone.restaurants.length === 0 ? (
          <div className="rounded-xl border border-border/50 bg-muted/20 p-6 text-center text-sm text-muted-foreground">
            لسه مفيش مطاعم في الزون ده
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {activeZone.restaurants.map((restaurant) => {
              const mapHref = buildMapHref(restaurant);
              return (
                <Card
                  key={restaurant.id}
                  className="cursor-pointer border-border/50 transition hover:border-primary/40"
                  onClick={() => void openRestaurant(restaurant)}
                >
                  <CardHeader className="pb-2">
                    <div className="flex items-start justify-between gap-3">
                      <CardTitle className="text-base font-heading">
                        <Store size={16} className="ml-1 inline text-primary" />
                        {restaurant.name}
                      </CardTitle>
                      <Badge variant="outline" className={STATUS_COLOR[restaurant.status]}>
                        {STATUS_LABEL[restaurant.status]}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2 pt-0">
                    {restaurant.location ? (
                      <p className="text-xs text-muted-foreground">
                        <MapPin size={12} className="ml-1 inline" />
                        {restaurant.location}
                      </p>
                    ) : null}
                    {restaurant.lastReportPreview ? (
                      <p className="line-clamp-2 text-xs text-muted-foreground">
                        آخر زيارة: {restaurant.lastReportPreview}
                      </p>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-2">
                      {restaurant.reportsCount > 0 ? (
                        <Badge variant="outline" className="bg-muted/30 text-[11px]">
                          <MessageSquare size={11} className="ml-1 inline" />
                          {restaurant.reportsCount} زيارة
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="bg-muted/30 text-[11px]">
                          ما اتزرتش لسه
                        </Badge>
                      )}
                      {mapHref ? (
                        <Button
                          asChild
                          variant="outline"
                          size="sm"
                          className="h-7 rounded-lg text-[11px]"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <a href={mapHref} target="_blank" rel="noreferrer">
                            <MapPin size={11} className="ml-1" />
                            خرايط
                          </a>
                        </Button>
                      ) : null}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  // Zone list view (default)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatCard label="إجمالي المطاعم" value={totals.restaurants} tone="primary" />
        <StatCard label="ما اتزرتش" value={totals.pending} tone="amber" />
        <StatCard label="في الشغل" value={totals.inProgress} tone="blue" />
        <StatCard label="متابعة" value={totals.followUp} tone="purple" />
        <StatCard label="اتقفل" value={totals.won} tone="emerald" />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {zones.map((zone) => (
          <Card
            key={zone.id}
            className="cursor-pointer border-border/50 transition hover:border-primary/40"
            onClick={() => setActiveZoneId(zone.id)}
          >
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="flex items-center gap-2 text-base font-heading">
                  <span
                    className="inline-block h-3 w-3 rounded-full"
                    style={{ backgroundColor: zone.color }}
                  />
                  {zone.name}
                </CardTitle>
                <Badge variant="outline" className="bg-muted/40 text-xs">
                  {zone.restaurantCount} مطعم
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-2 pt-0">
              {zone.description ? (
                <p className="line-clamp-2 text-sm text-foreground/80">{zone.description}</p>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="bg-emerald-50 text-emerald-700 text-[11px]">
                  {zone.wonCount} اتقفل
                </Badge>
                <Badge variant="outline" className="bg-blue-50 text-blue-700 text-[11px]">
                  {zone.reportsCount} زيارة
                </Badge>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
};

const outcomeToStatus = (outcome: VisitOutcome): RestaurantStatus => {
  if (outcome === "won") return "won";
  if (outcome === "lost") return "lost";
  if (outcome === "follow_up") return "follow_up";
  return "in_progress";
};

interface RestaurantDetailProps {
  restaurant: ZoneRestaurant;
  reports: VisitReport[];
  reportsLoading: boolean;
  form: FormState;
  setForm: React.Dispatch<React.SetStateAction<FormState>>;
  submitting: boolean;
  onBack: () => void;
  onPhotosChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onRemovePhoto: (index: number) => void;
  onSubmit: () => void;
}

const RestaurantDetail = ({
  restaurant,
  reports,
  reportsLoading,
  form,
  setForm,
  submitting,
  onBack,
  onPhotosChange,
  onRemovePhoto,
  onSubmit,
}: RestaurantDetailProps) => {
  const mapHref = buildMapHref(restaurant);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" className="gap-1 rounded-lg" onClick={onBack}>
            <ChevronLeft size={14} />
            رجوع
          </Button>
          <h2 className="font-heading text-lg font-bold">{restaurant.name}</h2>
          <Badge variant="outline" className={STATUS_COLOR[restaurant.status]}>
            {STATUS_LABEL[restaurant.status]}
          </Badge>
        </div>
        {mapHref ? (
          <Button asChild size="sm" variant="outline" className="rounded-lg">
            <a href={mapHref} target="_blank" rel="noreferrer">
              <MapPin size={13} className="ml-1" />
              فتح الخريطة
            </a>
          </Button>
        ) : null}
      </div>
      {restaurant.location ? (
        <p className="text-sm text-muted-foreground">
          <MapPin size={12} className="ml-1 inline" />
          {restaurant.location}
        </p>
      ) : null}

      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Send size={16} className="text-primary" />
            تسجيل زيارة جديدة
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-xs">اسم اللي قابلته</Label>
              <Input
                value={form.contactName}
                onChange={(event) => setForm((current) => ({ ...current, contactName: event.target.value }))}
                className="rounded-xl"
                placeholder="مثلاً: أحمد المالك"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">منصبه / دوره</Label>
              <Input
                value={form.contactRole}
                onChange={(event) => setForm((current) => ({ ...current, contactRole: event.target.value }))}
                className="rounded-xl"
                placeholder="مالك / مدير / كاشير..."
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">رقمه (اختياري)</Label>
              <Input
                value={form.contactPhone}
                onChange={(event) => setForm((current) => ({ ...current, contactPhone: event.target.value }))}
                className="rounded-xl"
                dir="ltr"
                placeholder="+201XXXXXXXXX"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">نتيجة الزيارة</Label>
              <Select
                value={form.outcome}
                onValueChange={(value) => setForm((current) => ({ ...current, outcome: value as VisitOutcome }))}
              >
                <SelectTrigger className="rounded-xl">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(OUTCOME_LABEL).map(([key, label]) => (
                    <SelectItem key={key} value={key}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1">
            <Label className="text-xs">إيه اللي حصل بالظبط</Label>
            <Textarea
              value={form.whatHappened}
              onChange={(event) => setForm((current) => ({ ...current, whatHappened: event.target.value }))}
              className="min-h-[120px] rounded-xl"
              placeholder="احكي تفاصيل اللي حصل، اعتراضات المطعم، طلباته، أي شيء مهم..."
            />
          </div>

          <div className="space-y-1">
            <Label className="text-xs">الخطوة الجاية (اختياري)</Label>
            <Input
              value={form.nextStep}
              onChange={(event) => setForm((current) => ({ ...current, nextStep: event.target.value }))}
              className="rounded-xl"
              placeholder="مثلاً: ميعاد جديد الجمعة، أبعتله العرض بالواتس، إلخ..."
            />
          </div>

          <div className="space-y-2 rounded-xl border border-dashed border-border/60 bg-muted/10 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Label className="cursor-pointer">
                <Input
                  type="file"
                  accept="image/*"
                  capture="environment"
                  multiple
                  className="hidden"
                  onChange={onPhotosChange}
                />
                <span className="inline-flex items-center gap-1 rounded-lg bg-primary/10 px-3 py-1.5 text-xs text-primary">
                  <Camera size={14} />
                  صور بكاميرا
                </span>
              </Label>
              <Label className="cursor-pointer">
                <Input
                  type="file"
                  accept="image/*"
                  multiple
                  className="hidden"
                  onChange={onPhotosChange}
                />
                <span className="inline-flex items-center gap-1 rounded-lg bg-muted/40 px-3 py-1.5 text-xs">
                  <ImagePlus size={14} />
                  من الجاليري
                </span>
              </Label>
              <span className="text-[11px] text-muted-foreground">{form.photos.length}/6 صورة</span>
            </div>
            {form.photos.length > 0 ? (
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                {form.photos.map((file, index) => (
                  <div key={index} className="relative aspect-square overflow-hidden rounded-lg border border-border/50">
                    <img
                      src={URL.createObjectURL(file)}
                      alt={`photo-${index}`}
                      className="h-full w-full object-cover"
                    />
                    <button
                      type="button"
                      className="absolute left-1 top-1 rounded-full bg-black/60 p-1 text-white"
                      onClick={() => onRemovePhoto(index)}
                    >
                      <Trash2 size={10} />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={form.useGps}
              onChange={(event) => setForm((current) => ({ ...current, useGps: event.target.checked }))}
              className="h-4 w-4"
            />
            ضيف موقعي الحالي للتقرير (GPS)
          </label>

          <Button
            onClick={onSubmit}
            disabled={submitting}
            className="w-full gap-2 rounded-xl bg-primary text-primary-foreground"
          >
            {submitting ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
            احفظ الزيارة
          </Button>
        </CardContent>
      </Card>

      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base font-heading">سجل الزيارات السابقة</CardTitle>
        </CardHeader>
        <CardContent>
          {reportsLoading ? (
            <div className="flex items-center justify-center text-xs text-muted-foreground">
              <Loader2 size={14} className="ml-2 animate-spin text-primary" />
              بنحمل التقارير...
            </div>
          ) : reports.length === 0 ? (
            <p className="rounded-xl border border-border/50 bg-muted/20 p-4 text-center text-sm text-muted-foreground">
              مفيش زيارات سابقة لسه
            </p>
          ) : (
            <div className="space-y-3">
              {reports.map((report) => (
                <ReportRow key={report.id} report={report} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

const ReportRow = ({ report }: { report: VisitReport }) => (
  <div className="space-y-2 rounded-xl border border-border/50 bg-card p-3">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className={`${OUTCOME_COLOR[report.outcome]} border-transparent`}>
          {OUTCOME_LABEL[report.outcome]}
        </Badge>
        {report.contactName ? (
          <span className="text-muted-foreground">
            مع: <span className="font-medium text-foreground">{report.contactName}</span>
            {report.contactRole ? ` (${report.contactRole})` : null}
          </span>
        ) : null}
      </div>
      <span className="text-muted-foreground">{formatDateTimeAr(report.createdAt)}</span>
    </div>
    {report.whatHappened ? (
      <p className="whitespace-pre-wrap text-sm text-foreground/90">{report.whatHappened}</p>
    ) : null}
    {report.nextStep ? (
      <p className="text-xs text-muted-foreground">
        الخطوة الجاية: <span className="text-foreground/80">{report.nextStep}</span>
      </p>
    ) : null}
    {report.contactPhone ? (
      <p className="text-xs text-muted-foreground">
        <Phone size={11} className="ml-1 inline" />
        <span dir="ltr">{report.contactPhone}</span>
      </p>
    ) : null}
    {report.lat != null && report.lng != null ? (
      <p className="text-xs text-muted-foreground">
        <MapPin size={11} className="ml-1 inline" />
        <a
          href={`https://www.google.com/maps/search/?api=1&query=${report.lat},${report.lng}`}
          target="_blank"
          rel="noreferrer"
          className="underline"
          dir="ltr"
        >
          {report.lat.toFixed(5)}, {report.lng.toFixed(5)}
        </a>
      </p>
    ) : null}
    {report.photos.length > 0 ? (
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {report.photos.map((photo) => (
          <VisitPhotoImg key={photo.id} url={photo.url} alt={`photo-${photo.id}`} />
        ))}
      </div>
    ) : null}
    <p className="text-[11px] text-muted-foreground">— {report.repName}</p>
  </div>
);

const StatCard = ({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "primary" | "amber" | "blue" | "emerald" | "purple";
}) => {
  const palette: Record<typeof tone, string> = {
    primary: "bg-primary/10 text-primary border-primary/20",
    amber: "bg-amber-50 text-amber-700 border-amber-100",
    blue: "bg-blue-50 text-blue-700 border-blue-100",
    emerald: "bg-emerald-50 text-emerald-700 border-emerald-100",
    purple: "bg-purple-50 text-purple-700 border-purple-100",
  };
  return (
    <div className={`rounded-xl border p-3 text-center ${palette[tone]}`}>
      <p className="text-xs">{label}</p>
      <p className="mt-1 text-2xl font-bold">{value}</p>
    </div>
  );
};

export default MyZonesTab;
