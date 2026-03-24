import { useEffect, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  FileText,
  Loader2,
  PhoneCall,
  Search,
  Settings,
  ShoppingCart,
  UserCog,
  Users,
  UtensilsCrossed,
} from "lucide-react";
import { motion } from "framer-motion";

import { useToast } from "@/hooks/use-toast";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import {
  fetchCalls,
  fetchDashboardStats,
  fetchOrders,
  fetchUsers,
  subscribeToOrderEvents,
  updateOrderStatus,
  type Call,
  type DashboardStats,
  type Order,
  type OrderStreamState,
  type UserProfile,
} from "@/services/api";
import MenuTab from "@/components/dashboard/MenuTab";
import SettingsTab from "@/components/dashboard/SettingsTab";
import EmployeesTab from "@/components/dashboard/EmployeesTab";
import AnalyticsTab from "@/components/dashboard/AnalyticsTab";
import IssueReportsTab from "@/components/dashboard/IssueReportsTab";
import CustomersTab from "@/components/dashboard/CustomersTab";

const ORDER_STATUSES = [
  { value: "received", label: "تم الاستلام" },
  { value: "preparing", label: "بتتجهز" },
  { value: "ready", label: "جاهز" },
  { value: "out_for_delivery", label: "في الطريق" },
  { value: "delivered", label: "تم التوصيل" },
];

const EMPTY_STATS: DashboardStats = {
  totalCalls: "0",
  totalOrders: "0",
  totalCustomers: "0",
  totalFiles: "0",
};

const orderStatusBadge = (status: string) => {
  const map: Record<string, { label: string; className: string }> = {
    received: { label: "تم الاستلام", className: "bg-blue-100 text-blue-700 border-blue-200" },
    preparing: { label: "بتتجهز", className: "bg-amber-100 text-amber-700 border-amber-200" },
    ready: { label: "جاهز", className: "bg-purple-100 text-purple-700 border-purple-200" },
    out_for_delivery: { label: "في الطريق", className: "bg-orange-100 text-orange-700 border-orange-200" },
    delivered: { label: "تم التوصيل", className: "bg-emerald-100 text-emerald-700 border-emerald-200" },
    completed: { label: "تمت", className: "bg-emerald-100 text-emerald-700 border-emerald-200" },
    in_progress: { label: "بتتجهز", className: "bg-amber-100 text-amber-700 border-amber-200" },
    pending: { label: "مستنية", className: "bg-blue-100 text-blue-700 border-blue-200" },
  };
  const state = map[status] || { label: status, className: "bg-muted text-muted-foreground border-border" };
  return (
    <Badge variant="outline" className={state.className}>
      {state.label}
    </Badge>
  );
};

const callStatusBadge = (status: string) => {
  const map: Record<string, { label: string; className: string }> = {
    active: { label: "جاري", className: "bg-emerald-100 text-emerald-700 border-emerald-200" },
    closed: { label: "خلصت", className: "bg-muted text-muted-foreground border-border" },
    pending: { label: "مستنية", className: "bg-amber-100 text-amber-700 border-amber-200" },
  };
  const state = map[status] || map.pending;
  return (
    <Badge variant="outline" className={state.className}>
      {state.label}
    </Badge>
  );
};

interface OwnerDashboardViewProps {
  restaurantName: string;
  readOnly?: boolean;
  restaurantId?: number;
}

const OwnerDashboardView = ({ restaurantName, readOnly, restaurantId }: OwnerDashboardViewProps) => {
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [selectedChat, setSelectedChat] = useState<number | null>(null);
  const [calls, setCalls] = useState<Call[]>([]);
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [stats, setStats] = useState<DashboardStats>(EMPTY_STATS);
  const [driverPhone, setDriverPhone] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [savingOrderId, setSavingOrderId] = useState<string | null>(null);
  const [orderStreamState, setOrderStreamState] = useState<OrderStreamState>("connecting");

  useEffect(() => {
    let ignore = false;

    const loadDashboard = async () => {
      try {
        setLoading(true);
        const [callsData, usersData, ordersData, statsData] = await Promise.all([
          fetchCalls(restaurantId),
          fetchUsers(restaurantId),
          fetchOrders(restaurantId),
          fetchDashboardStats(restaurantId),
        ]);

        if (ignore) {
          return;
        }

        setCalls(callsData);
        setUsers(usersData);
        setOrders(ordersData);
        setStats(statsData);
        setDriverPhone(
          Object.fromEntries(
            ordersData
              .filter((order) => Boolean(order.driverPhone))
              .map((order) => [order.id, order.driverPhone ?? ""]),
          ),
        );
      } catch (error) {
        console.error("Failed to load owner dashboard:", error);
        if (!ignore) {
          toast({
            title: "خطأ",
            description: "مش قادرين نجيب بيانات المطعم دلوقتي",
            variant: "destructive",
          });
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    };

    void loadDashboard();
    return () => {
      ignore = true;
    };
  }, [restaurantId, toast]);

  useEffect(() => {
    if (selectedChat !== null && !calls.some((call) => call.id === selectedChat)) {
      setSelectedChat(null);
    }
  }, [calls, selectedChat]);

  useEffect(() => {
    let ignore = false;

    const refreshDashboardMeta = async () => {
      try {
        const [callsData, usersData, statsData] = await Promise.all([
          fetchCalls(restaurantId),
          fetchUsers(restaurantId),
          fetchDashboardStats(restaurantId),
        ]);

        if (ignore) {
          return;
        }

        setCalls(callsData);
        setUsers(usersData);
        setStats(statsData);
      } catch (error) {
        console.error("Failed to refresh dashboard meta from stream:", error);
      }
    };

    const unsubscribe = subscribeToOrderEvents({
      restaurantId,
      onStateChange: (state) => {
        if (!ignore) {
          setOrderStreamState(state);
        }
      },
      onEvent: (event) => {
        if (ignore) {
          return;
        }

        setOrders((current) => {
          const existingIndex = current.findIndex((entry) => entry.id === event.order.id);
          if (existingIndex === -1) {
            return [event.order, ...current];
          }
          return current.map((entry) => (entry.id === event.order.id ? event.order : entry));
        });
        setDriverPhone((current) => ({
          ...current,
          [event.order.id]: event.order.driverPhone ?? "",
        }));
        void refreshDashboardMeta();
      },
      onError: () => {
        if (!ignore) {
          console.warn("Order stream connection is retrying");
        }
      },
    });

    return () => {
      ignore = true;
      unsubscribe();
    };
  }, [restaurantId]);

  const persistOrder = async (orderId: string, status: string, driver?: string) => {
    const updated = await updateOrderStatus(orderId, status, driver);
    setOrders((current) => current.map((entry) => (entry.id === orderId ? updated : entry)));
    setDriverPhone((current) => ({
      ...current,
      [orderId]: updated.driverPhone ?? "",
    }));
  };

  const handleStatusChange = async (order: Order, newStatus: string) => {
    if (readOnly) {
      return;
    }

    const previousOrders = orders;
    const nextDriverPhone = newStatus === "out_for_delivery" ? driverPhone[order.id] ?? order.driverPhone ?? "" : undefined;

    setSavingOrderId(order.id);
    setOrders((current) =>
      current.map((entry) =>
        entry.id === order.id
          ? {
              ...entry,
              status: newStatus as Order["status"],
              driverPhone: nextDriverPhone ?? null,
            }
          : entry,
      ),
    );

    try {
      await persistOrder(order.id, newStatus, nextDriverPhone);
    } catch (error) {
      console.error("Failed to update order status:", error);
      setOrders(previousOrders);
      toast({
        title: "خطأ",
        description: "تحديث حالة الأوردر فشل، حاول تاني",
        variant: "destructive",
      });
    } finally {
      setSavingOrderId(null);
    }
  };

  const handleDriverPhoneBlur = async (order: Order) => {
    if (readOnly || order.status !== "out_for_delivery") {
      return;
    }

    const nextDriverPhone = driverPhone[order.id] ?? "";
    if (nextDriverPhone === (order.driverPhone ?? "")) {
      return;
    }

    const previousOrders = orders;
    setSavingOrderId(order.id);
    setOrders((current) =>
      current.map((entry) =>
        entry.id === order.id
          ? {
              ...entry,
              driverPhone: nextDriverPhone,
            }
          : entry,
      ),
    );

    try {
      await persistOrder(order.id, order.status, nextDriverPhone);
    } catch (error) {
      console.error("Failed to update driver phone:", error);
      setOrders(previousOrders);
      setDriverPhone((current) => ({
        ...current,
        [order.id]: order.driverPhone ?? "",
      }));
      toast({
        title: "خطأ",
        description: "رقم السائق ما اتحفظش، راجعه وحاول تاني",
        variant: "destructive",
      });
    } finally {
      setSavingOrderId(null);
    }
  };

  const filteredCalls = calls.filter((call) => call.phone.includes(search));
  const selectedCall = selectedChat ? calls.find((call) => call.id === selectedChat) ?? null : null;

  if (loading) {
    return (
      <Card className="border-border/50">
        <CardContent className="flex min-h-[320px] items-center justify-center gap-3">
          <Loader2 size={18} className="animate-spin text-primary" />
          <span className="text-sm text-muted-foreground">بنجهز بيانات {restaurantName}...</span>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          { label: "المكالمات", value: stats.totalCalls, icon: PhoneCall, color: "text-blue-600" },
          { label: "الأوردرات", value: stats.totalOrders, icon: ShoppingCart, color: "text-primary" },
          { label: "العملاء", value: stats.totalCustomers, icon: Users, color: "text-purple-600" },
          { label: "الملفات", value: stats.totalFiles, icon: FileText, color: "text-emerald-600" },
        ].map((stat) => (
          <Card key={stat.label} className="border-border/50">
            <CardContent className="flex items-center gap-3 p-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-muted">
                <stat.icon size={20} className={stat.color} />
              </div>
              <div>
                <p className="text-2xl font-heading font-bold text-foreground">{stat.value}</p>
                <p className="text-xs text-muted-foreground">{stat.label}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Tabs defaultValue="calls" className="space-y-4">
        <TabsList className="flex flex-wrap rounded-xl bg-muted/50 p-1">
          <TabsTrigger value="calls" className="gap-1.5 rounded-lg data-[state=active]:shadow-sm">
            <PhoneCall size={15} /> المكالمات
          </TabsTrigger>
          <TabsTrigger value="orders" className="gap-1.5 rounded-lg data-[state=active]:shadow-sm">
            <ShoppingCart size={15} /> الأوردرات
          </TabsTrigger>
          <TabsTrigger value="customers" className="gap-1.5 rounded-lg data-[state=active]:shadow-sm">
            <Users size={15} /> العملاء
          </TabsTrigger>
          <TabsTrigger value="menu" className="gap-1.5 rounded-lg data-[state=active]:shadow-sm">
            <UtensilsCrossed size={15} /> المنيو
          </TabsTrigger>
          <TabsTrigger value="issues" className="gap-1.5 rounded-lg data-[state=active]:shadow-sm">
            <AlertTriangle size={15} /> البلاغات
          </TabsTrigger>
          <TabsTrigger value="analytics" className="gap-1.5 rounded-lg data-[state=active]:shadow-sm">
            <BarChart3 size={15} /> الإحصائيات
          </TabsTrigger>
          {!readOnly && (
            <TabsTrigger value="employees" className="gap-1.5 rounded-lg data-[state=active]:shadow-sm">
              <UserCog size={15} /> الموظفين
            </TabsTrigger>
          )}
          <TabsTrigger value="settings" className="gap-1.5 rounded-lg data-[state=active]:shadow-sm">
            <Settings size={15} /> الإعدادات
          </TabsTrigger>
        </TabsList>

        <TabsContent value="calls">
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-3 md:col-span-1">
              <div className="relative">
                <Search size={16} className="absolute start-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="ابحث برقم الموبايل..."
                  className="w-full rounded-xl border border-border bg-muted/50 px-4 py-2.5 pe-4 ps-10 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
              {filteredCalls.map((call) => (
                <motion.div
                  key={call.id}
                  whileHover={{ scale: 1.01 }}
                  onClick={() => setSelectedChat(call.id)}
                  className={`cursor-pointer rounded-xl border p-4 transition-colors ${
                    selectedChat === call.id ? "border-primary/40 bg-accent" : "border-border/50 bg-card hover:bg-muted/30"
                  }`}
                >
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-medium text-foreground" dir="ltr">
                      {call.phone}
                    </span>
                    {callStatusBadge(call.status)}
                  </div>
                  <p className="truncate text-xs text-muted-foreground">{call.lastMessage}</p>
                  <div className="mt-1 flex items-center justify-between">
                    <p className="text-xs text-muted-foreground/60">{call.time}</p>
                    <p className="text-xs font-medium text-primary">{call.orderTotal}</p>
                  </div>
                </motion.div>
              ))}
              {filteredCalls.length === 0 && (
                <Card className="border-border/50">
                  <CardContent className="py-8 text-center text-sm text-muted-foreground">مفيش مكالمات مطابقة للبحث</CardContent>
                </Card>
              )}
            </div>

            <div className="md:col-span-2">
              {selectedCall ? (
                <Card className="h-full border-border/50">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-base font-heading">مكالمة مع</CardTitle>
                      <span className="text-sm font-medium text-foreground" dir="ltr">
                        {selectedCall.phone}
                      </span>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="flex justify-start">
                      <div className="max-w-xs rounded-2xl rounded-tr-sm bg-muted px-4 py-3">
                        <p className="text-sm">{selectedCall.lastMessage}</p>
                        <p className="mt-1 text-[10px] text-muted-foreground">العميل</p>
                      </div>
                    </div>
                    <div className="flex justify-end">
                      <div className="max-w-xs rounded-2xl rounded-tl-sm bg-primary px-4 py-3">
                        <p className="text-sm text-primary-foreground">{selectedCall.aiResponse}</p>
                        <p className="mt-1 text-[10px] text-primary-foreground/70">ألو إيچي</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ) : (
                <Card className="flex min-h-[300px] items-center justify-center border-border/50">
                  <div className="text-center text-muted-foreground">
                    <PhoneCall size={48} className="mx-auto mb-3 opacity-30" />
                    <p className="text-sm">اختار مكالمة عشان تشوف التفاصيل</p>
                  </div>
                </Card>
              )}
            </div>
          </div>
        </TabsContent>

        <TabsContent value="orders">
          <Card className="border-border/50">
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
              <CardTitle className="text-base font-heading">الأوردرات</CardTitle>
                <Badge
                  variant="outline"
                  className={
                    orderStreamState === "live"
                      ? "border-emerald-200 bg-emerald-100 text-emerald-700"
                      : orderStreamState === "reconnecting"
                        ? "border-amber-200 bg-amber-100 text-amber-700"
                        : "border-blue-200 bg-blue-100 text-blue-700"
                  }
                >
                  {orderStreamState === "live"
                    ? "Live"
                    : orderStreamState === "reconnecting"
                      ? "بيحاول يعيد الاتصال"
                      : "بيوصل"}
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm" dir="rtl">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="px-4 py-3 text-start font-medium text-muted-foreground">رقم الأوردر</th>
                      <th className="px-4 py-3 text-start font-medium text-muted-foreground">رقم الموبايل</th>
                      <th className="px-4 py-3 text-start font-medium text-muted-foreground">الأصناف</th>
                      <th className="px-4 py-3 text-start font-medium text-muted-foreground">المبلغ</th>
                      <th className="px-4 py-3 text-start font-medium text-muted-foreground">الحالة</th>
                      <th className="px-4 py-3 text-start font-medium text-muted-foreground">السائق</th>
                      <th className="px-4 py-3 text-start font-medium text-muted-foreground">التاريخ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((order) => (
                      <tr key={order.id} className="border-b border-border/50 transition-colors hover:bg-muted/30">
                        <td className="px-4 py-3 font-mono text-xs" dir="ltr">
                          {order.id}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs" dir="ltr">
                          {order.phone}
                        </td>
                        <td className="px-4 py-3 text-muted-foreground">{order.items}</td>
                        <td className="px-4 py-3 font-medium">{order.amount}</td>
                        <td className="px-3 py-3">
                          <div className="flex items-center gap-2">
                            <Select
                              value={order.status}
                              onValueChange={(value) => {
                                void handleStatusChange(order, value);
                              }}
                              disabled={Boolean(readOnly || savingOrderId === order.id)}
                            >
                              <SelectTrigger className="h-8 w-32 rounded-lg text-xs">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {ORDER_STATUSES.map((status) => (
                                  <SelectItem key={status.value} value={status.value}>
                                    {status.label}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            {orderStatusBadge(order.status)}
                          </div>
                        </td>
                        <td className="px-3 py-3">
                          {order.status === "out_for_delivery" ? (
                            <Input
                              value={driverPhone[order.id] ?? order.driverPhone ?? ""}
                              onChange={(event) =>
                                setDriverPhone((current) => ({
                                  ...current,
                                  [order.id]: event.target.value,
                                }))
                              }
                              onBlur={() => {
                                void handleDriverPhoneBlur(order);
                              }}
                              onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                  event.currentTarget.blur();
                                }
                              }}
                              placeholder="رقم السائق"
                              className="h-8 w-28 rounded-lg text-xs"
                              dir="ltr"
                              disabled={Boolean(readOnly || savingOrderId === order.id)}
                            />
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-xs text-muted-foreground" dir="ltr">
                          {order.date}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {orders.length === 0 && <div className="py-8 text-center text-sm text-muted-foreground">مفيش أوردرات لسه</div>}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="customers">
          <CustomersTab users={users} orders={orders} />
        </TabsContent>

        <TabsContent value="menu">
          <MenuTab restaurantId={restaurantId} readOnly={readOnly} />
        </TabsContent>

        <TabsContent value="issues">
          <IssueReportsTab restaurantId={restaurantId} readOnly={readOnly} />
        </TabsContent>

        <TabsContent value="analytics">
          <AnalyticsTab restaurantId={restaurantId} />
        </TabsContent>

        {!readOnly && (
          <TabsContent value="employees">
            <EmployeesTab restaurantId={restaurantId} />
          </TabsContent>
        )}

        <TabsContent value="settings">
          <SettingsTab readOnly={readOnly} restaurantId={restaurantId} />
        </TabsContent>
      </Tabs>
    </>
  );
};

export default OwnerDashboardView;
