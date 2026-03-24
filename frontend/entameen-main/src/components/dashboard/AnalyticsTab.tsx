import { useEffect, useState } from "react";
import { BarChart, Bar, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BarChart3, Clock, DollarSign, TrendingUp } from "lucide-react";

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
};

const AnalyticsTab = ({ restaurantId }: AnalyticsTabProps) => {
  const [analytics, setAnalytics] = useState<AnalyticsResponse>(EMPTY_ANALYTICS);

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

      <Card className="max-w-md border-border/50">
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
    </div>
  );
};

export default AnalyticsTab;
