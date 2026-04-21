import { useEffect, useMemo, useState } from "react";
import { BarChart, Bar, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BarChart3, CheckCircle2, Clock, DollarSign, ShieldCheck, TrendingUp, AlertTriangle } from "lucide-react";

import { fetchAnalytics, type AnalyticsResponse } from "@/services/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const COLORS = ["hsl(224, 82%, 51%)", "hsl(224, 75%, 62%)", "hsl(224, 90%, 70%)", "hsl(220, 15%, 70%)"];

interface AnalyticsTabProps {
  restaurantId?: number;
}

const EMPTY_ANALYTICS: AnalyticsResponse = {
  summary: {
    totalRevenue: "0 ج.م",
    avgOrder: "0 ج.م",
    todayOrders: "0",
    responseTime: "0 ثانية",
  },
  dailyOrders: [],
  monthlyRevenue: [],
  categoryData: [],
  quality: {
    summary: {
      totalCalls: 0,
      successfulCalls: 0,
      reviewedCalls: 0,
      needsReview: 0,
      successRate: "0%",
      reviewCoverage: "0%",
    },
    outcomes: [],
    failures: [],
    reviewStatuses: [],
    topBlockers: [],
  },
};

const OUTCOME_LABELS: Record<string, string> = {
  order_confirmed: "طلب متقفل",
  reservation_confirmed: "حجز متقفل",
  complaint_logged: "شكوى متسجلة",
  handoff: "تحويل",
  abandoned: "العميل سابها",
  failed: "فشلت",
  closed_without_action: "مفيش إجراء",
  unknown: "غير معروف",
};

const REVIEW_STATUS_LABELS: Record<string, string> = {
  reviewed: "متراجعة",
  ignored: "متخطية",
  needs_review: "محتاجة مراجعة",
};

const FAILURE_REASON_LABELS: Record<string, string> = {
  customer_inactive: "العميل سكت وماكملش",
  max_duration_reached: "المكالمة طولت عن الحد",
  session_error: "فيه خطأ تقني",
  no_order_items: "مافيش طلب واضح",
  missing_name: "الاسم ناقص",
  missing_phone: "رقم الموبايل ناقص",
  missing_address: "العنوان ناقص",
  order_not_confirmed: "الطلب ما اتأكدش",
  missing_reservation_time: "ميعاد الحجز ناقص",
  missing_guests_count: "عدد الأفراد ناقص",
  missing_branch: "الفرع ناقص",
  reservation_not_confirmed: "الحجز ما اتأكدش",
  missing_complaint_text: "تفاصيل الشكوى ناقصة",
  missing_complaint_type: "نوع الشكوى ناقص",
  complaint_not_submitted: "الشكوى ما اتثبتتش",
  ended_without_action: "المكالمة انتهت من غير نتيجة",
};

const AnalyticsTab = ({ restaurantId }: AnalyticsTabProps) => {
  const [analytics, setAnalytics] = useState<AnalyticsResponse>(EMPTY_ANALYTICS);

  const qualityOutcomes = useMemo(
    () =>
      analytics.quality.outcomes.map((item) => ({
        ...item,
        label: OUTCOME_LABELS[item.name] || item.name,
      })),
    [analytics.quality.outcomes],
  );

  const qualityReviewStatuses = useMemo(
    () =>
      analytics.quality.reviewStatuses.map((item) => ({
        ...item,
        label: REVIEW_STATUS_LABELS[item.name] || item.name,
      })),
    [analytics.quality.reviewStatuses],
  );

  const qualityFailures = useMemo(
    () =>
      analytics.quality.failures.map((item) => ({
        ...item,
        label: FAILURE_REASON_LABELS[item.name] || item.name,
      })),
    [analytics.quality.failures],
  );

  useEffect(() => {
    let ignore = false;

    const load = async () => {
      try {
        const data = await fetchAnalytics(restaurantId);
        if (!ignore) setAnalytics(data);
      } catch (error) {
        console.error("Failed to load analytics:", error);
      }
    };

    void load();
    return () => {
      ignore = true;
    };
  }, [restaurantId]);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          { label: "إجمالي الإيرادات", value: analytics.summary.totalRevenue, icon: DollarSign },
          { label: "متوسط الأوردر", value: analytics.summary.avgOrder, icon: TrendingUp },
          { label: "أوردرات اليوم", value: analytics.summary.todayOrders, icon: BarChart3 },
          { label: "وقت الاستجابة", value: analytics.summary.responseTime, icon: Clock },
        ].map((stat) => (
          <Card key={stat.label} className="border-border/50">
            <CardContent className="p-4">
              <div className="mb-2 flex items-center justify-between">
                <stat.icon size={18} className="text-primary" />
              </div>
              <p className="text-xl font-heading font-bold text-foreground">{stat.value}</p>
              <p className="text-xs text-muted-foreground">{stat.label}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          { label: "نسبة نجاح المكالمات", value: analytics.quality.summary.successRate, icon: CheckCircle2 },
          { label: "تغطية المراجعة", value: analytics.quality.summary.reviewCoverage, icon: ShieldCheck },
          { label: "مكالمات ناجحة", value: String(analytics.quality.summary.successfulCalls), icon: TrendingUp },
          { label: "في Queue المراجعة", value: String(analytics.quality.summary.needsReview), icon: AlertTriangle },
        ].map((stat) => (
          <Card key={stat.label} className="border-border/50">
            <CardContent className="p-4">
              <div className="mb-2 flex items-center justify-between">
                <stat.icon size={18} className="text-primary" />
              </div>
              <p className="text-xl font-heading font-bold text-foreground">{stat.value}</p>
              <p className="text-xs text-muted-foreground">{stat.label}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-heading">الأوردرات اليومية</CardTitle>
          </CardHeader>
          <CardContent>
            <div dir="ltr">
              <ResponsiveContainer width="100%" height={250}>
                <BarChart data={analytics.dailyOrders}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 90%)" />
                  <XAxis dataKey="day" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Bar dataKey="orders" fill="hsl(224, 82%, 51%)" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-heading">الإيرادات الشهرية</CardTitle>
          </CardHeader>
          <CardContent>
            <div dir="ltr">
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={analytics.monthlyRevenue}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 90%)" />
                  <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="revenue" stroke="hsl(224, 82%, 51%)" strokeWidth={3} dot={{ fill: "hsl(224, 82%, 51%)", r: 5 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-heading">Top Blockers للمكالمات</CardTitle>
          </CardHeader>
          <CardContent>
            {qualityFailures.length > 0 ? (
              <div dir="ltr">
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={qualityFailures.slice(0, 6)}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 90%)" />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} angle={-12} textAnchor="end" height={70} />
                    <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                    <Tooltip formatter={(value: number) => [`${value}`, "عدد المكالمات"]} />
                    <Bar dataKey="value" fill="hsl(18, 87%, 58%)" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="py-16 text-center text-sm text-muted-foreground">لحد دلوقتي مفيش blockers متسجلة</div>
            )}
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-heading">Review Queue</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-xl border border-border/50 bg-muted/20 p-4">
              <p className="text-sm font-medium text-foreground">إجمالي المكالمات</p>
              <p className="mt-1 text-2xl font-heading font-bold text-foreground">{analytics.quality.summary.totalCalls}</p>
            </div>
            {analytics.quality.topBlockers.length > 0 ? (
              analytics.quality.topBlockers.map((item) => (
                <div key={item.reason} className="flex items-center justify-between rounded-xl border border-border/50 bg-card px-4 py-3">
                  <div>
                    <p className="text-sm font-medium text-foreground">{FAILURE_REASON_LABELS[item.reason] || item.reason}</p>
                    <p className="text-xs text-muted-foreground">{item.reason}</p>
                  </div>
                  <span className="text-sm font-bold text-primary">{item.count}</span>
                </div>
              ))
            ) : (
              <div className="rounded-xl border border-border/50 bg-card px-4 py-6 text-center text-sm text-muted-foreground">
                مفيش أسباب فشل متكررة حاليًا
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-heading">توزيع الأصناف</CardTitle>
          </CardHeader>
          <CardContent>
            <div dir="ltr">
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={analytics.categoryData}
                    cx="50%"
                    cy="50%"
                    outerRadius={90}
                    dataKey="value"
                    label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                  >
                    {analytics.categoryData.map((_, index) => (
                      <Cell key={index} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-heading">Outcomes المكالمات</CardTitle>
          </CardHeader>
          <CardContent>
            {qualityOutcomes.length > 0 ? (
              <div dir="ltr">
                <ResponsiveContainer width="100%" height={250}>
                  <PieChart>
                    <Pie
                      data={qualityOutcomes}
                      cx="50%"
                      cy="50%"
                      outerRadius={90}
                      dataKey="value"
                      label={({ payload, percent }) => `${payload.label} ${(percent * 100).toFixed(0)}%`}
                    >
                      {qualityOutcomes.map((_, index) => (
                        <Cell key={index} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value: number, _name, item) => [`${value}`, item.payload.label]} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="py-16 text-center text-sm text-muted-foreground">مفيش calls كفاية لعرض outcomes</div>
            )}
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-heading">حالة المراجعة</CardTitle>
          </CardHeader>
          <CardContent>
            {qualityReviewStatuses.length > 0 ? (
              <div dir="ltr">
                <ResponsiveContainer width="100%" height={250}>
                  <BarChart data={qualityReviewStatuses}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 15%, 90%)" />
                    <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                    <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                    <Tooltip formatter={(value: number) => [`${value}`, "عدد المكالمات"]} />
                    <Bar dataKey="value" fill="hsl(224, 82%, 51%)" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="py-16 text-center text-sm text-muted-foreground">مفيش review status data متسجلة</div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default AnalyticsTab;
