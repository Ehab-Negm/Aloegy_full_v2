import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Award,
  Loader2,
  MapPin,
  Phone,
  RefreshCw,
  Store,
  TrendingUp,
  Users,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/hooks/use-toast";
import VisitPhotoImg from "@/components/sales/VisitPhotoImg";
import {
  fetchAdminSalesAnalytics,
  type SalesAnalytics,
  type VisitOutcome,
} from "@/services/api";

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

const fmtPct = (value: number) => `${(value * 100).toFixed(1)}%`;

const formatDateTimeAr = (iso: string): string => {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ar-EG", { dateStyle: "short", timeStyle: "short" });
};

const SalesAnalyticsSection = () => {
  const { toast } = useToast();
  const [data, setData] = useState<SalesAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      else setRefreshing(true);
      const next = await fetchAdminSalesAnalytics();
      setData(next);
    } catch (error) {
      console.error("Failed to load analytics", error);
      toast({ title: "خطأ", description: "مش قادرين نجيب التحاليل", variant: "destructive" });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sortedZones = useMemo(() => {
    if (!data) return [];
    return [...data.perZone].sort((a, b) => b.reports - a.reports);
  }, [data]);

  if (loading || !data) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-border/50 bg-muted/20 p-8 text-sm text-muted-foreground">
        <Loader2 size={16} className="ml-2 animate-spin text-primary" />
        بنحمل التحاليل...
      </div>
    );
  }

  const { totals, perRep, recentReports } = data;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="font-heading text-lg font-bold">تحاليل أداء فريق المبيعات</h2>
        <Button
          size="sm"
          variant="ghost"
          className="gap-1 rounded-lg"
          onClick={() => void load(true)}
          disabled={refreshing}
        >
          {refreshing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          تحديث
        </Button>
      </div>

      {/* Top KPIs */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <KpiCard label="الزونز" value={totals.zones} icon={<MapPin size={14} />} tone="primary" />
        <KpiCard label="المطاعم" value={totals.restaurants} icon={<Store size={14} />} tone="blue" />
        <KpiCard label="الزيارات" value={totals.reports} icon={<Activity size={14} />} tone="purple" />
        <KpiCard label="اتقفل" value={totals.won} icon={<Award size={14} />} tone="emerald" />
        <KpiCard label="اترفض" value={totals.lost} icon={<TrendingUp size={14} />} tone="rose" />
        <KpiCard
          label="نسبة التحويل"
          value={fmtPct(totals.conversionRate)}
          icon={<TrendingUp size={14} />}
          tone="amber"
        />
      </div>

      {/* Status breakdown bar */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base font-heading">توزيع المطاعم على الحالات</CardTitle>
        </CardHeader>
        <CardContent>
          <StatusBar totals={totals} />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Per-zone */}
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <MapPin size={16} className="text-primary" />
              أداء كل زون
            </CardTitle>
          </CardHeader>
          <CardContent>
            {sortedZones.length === 0 ? (
              <p className="rounded-xl border border-border/50 bg-muted/20 p-4 text-center text-sm text-muted-foreground">
                مفيش زونز لسه
              </p>
            ) : (
              <div className="space-y-3">
                {sortedZones.map((zone) => (
                  <div
                    key={zone.zoneId}
                    className="space-y-2 rounded-xl border border-border/50 bg-card p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span
                          className="inline-block h-3 w-3 rounded-full"
                          style={{ backgroundColor: zone.color }}
                        />
                        <p className="font-medium text-foreground">{zone.zoneName}</p>
                      </div>
                      <Badge variant="outline" className="bg-muted/40 text-xs">
                        {fmtPct(zone.conversionRate)} تحويل
                      </Badge>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                      <Stat compact label="مطاعم" value={zone.restaurants} />
                      <Stat compact label="زيارات" value={zone.reports} />
                      <Stat compact label="اتقفل" value={zone.won} tone="emerald" />
                      <Stat compact label="متابعة" value={zone.followUp} tone="purple" />
                    </div>
                    <ZoneMiniBar zone={zone} />
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Per-rep */}
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Users size={16} className="text-primary" />
              أداء كل مندوب
            </CardTitle>
          </CardHeader>
          <CardContent>
            {perRep.length === 0 ? (
              <p className="rounded-xl border border-border/50 bg-muted/20 p-4 text-center text-sm text-muted-foreground">
                مفيش مندوبين متسجلين
              </p>
            ) : (
              <div className="space-y-2">
                {perRep.map((rep, index) => (
                  <div
                    key={rep.repId}
                    className="flex flex-col gap-2 rounded-xl border border-border/50 bg-card p-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="flex items-center gap-3">
                      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                        {index + 1}
                      </span>
                      <div>
                        <p className="font-medium text-foreground">{rep.repName}</p>
                        <p className="text-xs text-muted-foreground" dir="ltr">
                          <Phone size={10} className="ml-1 inline" />
                          {rep.repPhone}
                        </p>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline" className="bg-blue-50 text-blue-700 text-[11px]">
                        {rep.zones} زون
                      </Badge>
                      <Badge variant="outline" className="bg-muted/40 text-[11px]">
                        {rep.reports} زيارة
                      </Badge>
                      <Badge variant="outline" className="bg-muted/30 text-[11px]">
                        {rep.lastReportAt ? `آخر نشاط: ${formatDateTimeAr(rep.lastReportAt)}` : "لسه مدخلش"}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Recent reports */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Activity size={16} className="text-primary" />
            آخر الزيارات (Activity Feed)
          </CardTitle>
        </CardHeader>
        <CardContent>
          {recentReports.length === 0 ? (
            <p className="rounded-xl border border-border/50 bg-muted/20 p-4 text-center text-sm text-muted-foreground">
              مفيش زيارات لسه
            </p>
          ) : (
            <div className="space-y-3">
              {recentReports.map((report) => (
                <div key={report.id} className="space-y-2 rounded-xl border border-border/50 bg-card p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-foreground">
                        <Store size={12} className="ml-1 inline" />
                        {report.restaurantName}
                      </span>
                      {report.zoneName ? (
                        <span className="text-muted-foreground">/ {report.zoneName}</span>
                      ) : null}
                      <Badge
                        variant="outline"
                        className={`${OUTCOME_COLOR[report.outcome]} border-transparent`}
                      >
                        {OUTCOME_LABEL[report.outcome]}
                      </Badge>
                    </div>
                    <span className="text-muted-foreground">{formatDateTimeAr(report.createdAt)}</span>
                  </div>
                  {report.contactName ? (
                    <p className="text-xs text-muted-foreground">
                      مع: <span className="font-medium text-foreground">{report.contactName}</span>
                      {report.contactRole ? ` (${report.contactRole})` : null}
                    </p>
                  ) : null}
                  {report.whatHappened ? (
                    <p className="line-clamp-3 whitespace-pre-wrap text-sm text-foreground/90">
                      {report.whatHappened}
                    </p>
                  ) : null}
                  {report.photos.length > 0 ? (
                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                      {report.photos.slice(0, 3).map((photo) => (
                        <VisitPhotoImg key={photo.id} url={photo.url} alt={`photo-${photo.id}`} />
                      ))}
                    </div>
                  ) : null}
                  <p className="text-[11px] text-muted-foreground">— {report.repName}</p>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

const StatusBar = ({ totals }: { totals: SalesAnalytics["totals"] }) => {
  const buckets: Array<{ key: string; label: string; value: number; color: string }> = [
    { key: "won", label: "اتقفل", value: totals.won, color: "#10B981" },
    { key: "in_progress", label: "في الشغل", value: totals.inProgress, color: "#3B82F6" },
    { key: "follow_up", label: "متابعة", value: totals.followUp, color: "#8B5CF6" },
    { key: "lost", label: "اترفض", value: totals.lost, color: "#EF4444" },
    { key: "pending", label: "ما اتزرتش", value: totals.pending, color: "#F59E0B" },
  ];
  const total = buckets.reduce((sum, bucket) => sum + bucket.value, 0) || 1;
  return (
    <div className="space-y-2">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {buckets.map((bucket) => (
          <div
            key={bucket.key}
            style={{
              backgroundColor: bucket.color,
              width: `${(bucket.value / total) * 100}%`,
            }}
            title={`${bucket.label}: ${bucket.value}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {buckets.map((bucket) => (
          <span key={bucket.key} className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: bucket.color }} />
            {bucket.label}: <span className="font-medium text-foreground">{bucket.value}</span>
          </span>
        ))}
      </div>
    </div>
  );
};

const ZoneMiniBar = ({ zone }: { zone: SalesAnalytics["perZone"][number] }) => {
  const total = zone.restaurants || 1;
  const segments: Array<{ value: number; color: string; label: string }> = [
    { value: zone.won, color: "#10B981", label: "اتقفل" },
    { value: zone.inProgress, color: "#3B82F6", label: "في الشغل" },
    { value: zone.followUp, color: "#8B5CF6", label: "متابعة" },
    { value: zone.lost, color: "#EF4444", label: "اترفض" },
    { value: zone.pending, color: "#F59E0B", label: "ما اتزرتش" },
  ];
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
      {segments.map((segment, index) =>
        segment.value > 0 ? (
          <div
            key={index}
            style={{
              backgroundColor: segment.color,
              width: `${(segment.value / total) * 100}%`,
            }}
            title={`${segment.label}: ${segment.value}`}
          />
        ) : null,
      )}
    </div>
  );
};

const KpiCard = ({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: number | string;
  icon: React.ReactNode;
  tone: "primary" | "blue" | "purple" | "emerald" | "rose" | "amber";
}) => {
  const palette: Record<typeof tone, string> = {
    primary: "bg-primary/10 text-primary border-primary/20",
    blue: "bg-blue-50 text-blue-700 border-blue-100",
    purple: "bg-purple-50 text-purple-700 border-purple-100",
    emerald: "bg-emerald-50 text-emerald-700 border-emerald-100",
    rose: "bg-rose-50 text-rose-700 border-rose-100",
    amber: "bg-amber-50 text-amber-700 border-amber-100",
  };
  return (
    <div className={`rounded-xl border p-3 ${palette[tone]}`}>
      <div className="flex items-center justify-between text-xs">
        <span>{label}</span>
        <span>{icon}</span>
      </div>
      <p className="mt-1 text-2xl font-bold">{value}</p>
    </div>
  );
};

const Stat = ({
  label,
  value,
  tone,
  compact = false,
}: {
  label: string;
  value: number | string;
  tone?: "emerald" | "purple";
  compact?: boolean;
}) => {
  const tonePalette: Record<NonNullable<typeof tone>, string> = {
    emerald: "text-emerald-700",
    purple: "text-purple-700",
  };
  return (
    <div className={`rounded-md ${compact ? "" : "p-2"}`}>
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className={`text-base font-bold ${tone ? tonePalette[tone] : "text-foreground"}`}>{value}</p>
    </div>
  );
};

export default SalesAnalyticsSection;
