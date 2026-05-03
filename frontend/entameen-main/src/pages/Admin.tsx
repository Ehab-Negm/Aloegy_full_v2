import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import {
  ArrowRight,
  BarChart3,
  CheckCircle,
  ChevronLeft,
  ClipboardList,
  Loader2,
  LogOut,
  Map as MapIcon,
  PhoneCall,
  Plus,
  ShoppingCart,
  Store,
  Trash2,
  UserPlus,
  Users,
  XCircle,
} from "lucide-react";

import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import logo from "@/assets/logo.png";
import OwnerDashboardView from "@/components/admin/OwnerDashboardView";
import ZoneManagementSection from "@/components/admin/ZoneManagementSection";
import SalesAnalyticsSection from "@/components/admin/SalesAnalyticsSection";
import {
  clearAuthSession,
  createAdminRestaurant,
  createAdminSalesMember,
  deleteAdminSalesMember,
  fetchAdminOverview,
  fetchAdminRestaurants,
  fetchAdminSalesRequests,
  fetchAdminSalesTeam,
  updateAdminSalesRequestStatus,
  type AdminOverview,
  type AdminRestaurant,
  type AdminSalesRequest,
  type SalesTeamMember,
} from "@/services/api";

const EMPTY_OVERVIEW: AdminOverview = {
  liveCalls: [],
  recentOrders: [],
};

const statusColor = (status: string) => {
  if (status === "active") return "bg-emerald-100 text-emerald-700 border-emerald-200";
  if (status === "suspended") return "bg-destructive/10 text-destructive border-destructive/20";
  return "bg-amber-100 text-amber-700 border-amber-200";
};

const statusLabel = (status: string) => {
  if (status === "active") return "نشط";
  if (status === "suspended") return "موقوف";
  return "معلق";
};

const salesRequestBadge = (status: AdminSalesRequest["status"]) => {
  if (status === "approved") return "bg-emerald-100 text-emerald-700 border-emerald-200";
  if (status === "rejected") return "bg-destructive/10 text-destructive border-destructive/20";
  return "bg-amber-100 text-amber-700 border-amber-200";
};

const recentOrderStatusLabel = (status: string) => {
  const labels: Record<string, string> = {
    received: "تم الاستلام",
    preparing: "بتتجهز",
    ready: "جاهز",
    out_for_delivery: "في الطريق",
    delivered: "تم التوصيل",
  };
  return labels[status] || status;
};

const Admin = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [selectedRestaurantId, setSelectedRestaurantId] = useState<number | null>(null);
  const [owners, setOwners] = useState<AdminRestaurant[]>([]);
  const [salesRequests, setSalesRequests] = useState<AdminSalesRequest[]>([]);
  const [salesTeam, setSalesTeam] = useState<SalesTeamMember[]>([]);
  const [overview, setOverview] = useState<AdminOverview>(EMPTY_OVERVIEW);
  const [loading, setLoading] = useState(true);
  const [showRegister, setShowRegister] = useState(false);
  const [submittingRestaurant, setSubmittingRestaurant] = useState(false);
  const [submittingSales, setSubmittingSales] = useState(false);
  const [salesActionId, setSalesActionId] = useState<number | null>(null);
  const [removingSalesId, setRemovingSalesId] = useState<number | null>(null);
  const [newRestaurant, setNewRestaurant] = useState({
    name: "",
    ownerName: "",
    ownerPhone: "",
    location: "",
    plan: "أساسي",
    assignedPhone: "",
  });
  const [newSalesPhone, setNewSalesPhone] = useState("");
  const [newSalesName, setNewSalesName] = useState("");

  const loadAdminData = useCallback(async () => {
    try {
      setLoading(true);
      const [restaurantsData, overviewData, salesTeamData, salesRequestsData] = await Promise.all([
        fetchAdminRestaurants(),
        fetchAdminOverview(),
        fetchAdminSalesTeam(),
        fetchAdminSalesRequests(),
      ]);
      setOwners(restaurantsData);
      setOverview(overviewData);
      setSalesTeam(salesTeamData);
      setSalesRequests(salesRequestsData);
    } catch (error) {
      console.error("Failed to load admin dashboard:", error);
      toast({
        title: "خطأ",
        description: "مش قادرين نجيب بيانات الأدمن دلوقتي",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void loadAdminData();
  }, [loadAdminData]);

  useEffect(() => {
    if (selectedRestaurantId !== null && !owners.some((owner) => owner.restaurantId === selectedRestaurantId)) {
      setSelectedRestaurantId(null);
    }
  }, [owners, selectedRestaurantId]);

  const selectedOwner = useMemo(
    () => owners.find((owner) => owner.restaurantId === selectedRestaurantId) ?? null,
    [owners, selectedRestaurantId],
  );

  const handleLogout = () => {
    clearAuthSession();
    navigate("/login", { replace: true });
  };

  const handleAddSales = async () => {
    if (!newSalesName.trim() || newSalesPhone.trim().length < 10) {
      toast({ title: "خطأ", description: "اكتب اسم ورقم موبايل صحيح", variant: "destructive" });
      return;
    }

    try {
      setSubmittingSales(true);
      await createAdminSalesMember(newSalesName, newSalesPhone);
      setNewSalesName("");
      setNewSalesPhone("");
      await loadAdminData();
      toast({ title: "تمت الإضافة", description: "عضو جديد اتضاف لفريق المبيعات" });
    } catch (error) {
      console.error("Failed to create sales member:", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نضيف عضو جديد دلوقتي",
        variant: "destructive",
      });
    } finally {
      setSubmittingSales(false);
    }
  };

  const handleRemoveSales = async (member: SalesTeamMember) => {
    try {
      setRemovingSalesId(member.id);
      await deleteAdminSalesMember(member.id, member.phone);
      await loadAdminData();
      toast({ title: "تم الحذف", description: "العضو اتحذف من فريق المبيعات" });
    } catch (error) {
      console.error("Failed to delete sales member:", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نحذف العضو دلوقتي",
        variant: "destructive",
      });
    } finally {
      setRemovingSalesId(null);
    }
  };

  const handleRegister = async () => {
    if (!newRestaurant.name.trim() || !newRestaurant.ownerName.trim() || !newRestaurant.ownerPhone.trim()) {
      toast({ title: "خطأ", description: "املا كل البيانات المطلوبة", variant: "destructive" });
      return;
    }

    try {
      setSubmittingRestaurant(true);
      await createAdminRestaurant(newRestaurant);
      setShowRegister(false);
      setNewRestaurant({
        name: "",
        ownerName: "",
        ownerPhone: "",
        location: "",
        plan: "أساسي",
        assignedPhone: "",
      });
      await loadAdminData();
      toast({ title: "تم التسجيل", description: "المطعم اتسجل بنجاح" });
    } catch (error) {
      console.error("Failed to create restaurant:", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نسجل المطعم دلوقتي",
        variant: "destructive",
      });
    } finally {
      setSubmittingRestaurant(false);
    }
  };

  const handleSalesAction = async (id: number, action: "approved" | "rejected") => {
    try {
      setSalesActionId(id);
      await updateAdminSalesRequestStatus(id, action);
      await loadAdminData();
      toast({
        title: action === "approved" ? "تم القبول" : "تم الرفض",
        description: action === "approved" ? "الطلب اتوافق عليه" : "الطلب اترفض",
      });
    } catch (error) {
      console.error("Failed to update sales request:", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نحدّث الطلب دلوقتي",
        variant: "destructive",
      });
    } finally {
      setSalesActionId(null);
    }
  };

  if (loading && owners.length === 0 && salesTeam.length === 0 && salesRequests.length === 0) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <Loader2 size={18} className="animate-spin text-primary" />
          بنجهز لوحة الأدمن...
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="glass sticky top-0 z-40 border-b border-border/50">
        <div className="container mx-auto flex h-14 items-center justify-between px-4">
          <div className="flex items-center gap-2">
            {selectedOwner && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setSelectedRestaurantId(null)}
                className="h-8 w-8 text-muted-foreground"
              >
                <ArrowRight size={16} />
              </Button>
            )}
            <Link to="/" className="flex items-center gap-2">
              <img src={logo} alt="ألو إيچي" className="h-7 w-7 object-contain" />
              <span className="text-lg font-heading font-bold text-foreground">ألو إيچي</span>
            </Link>
            <Badge className="bg-primary text-primary-foreground text-xs">أدمن</Badge>
          </div>
          <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={handleLogout}>
            <LogOut size={16} />
          </Button>
        </div>
      </div>

      <div className="container mx-auto px-4 py-6">
        {selectedOwner ? (
          <>
            <div className="mb-6 flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary shadow-brand">
                <Store size={24} className="text-primary-foreground" />
              </div>
              <div>
                <h1 className="text-2xl font-heading font-bold text-foreground">{selectedOwner.restaurantName}</h1>
                <p className="text-sm text-muted-foreground">
                  {selectedOwner.name} • <span dir="ltr">{selectedOwner.phone}</span> • {selectedOwner.location} • انضم {selectedOwner.joinedAt}
                </p>
              </div>
              <Badge variant="outline" className={statusColor(selectedOwner.status)}>
                {statusLabel(selectedOwner.status)}
              </Badge>
            </div>
            <OwnerDashboardView restaurantName={selectedOwner.restaurantName} restaurantId={selectedOwner.restaurantId} readOnly />
          </>
        ) : (
          <Tabs defaultValue="restaurants" className="space-y-4">
            <TabsList className="rounded-xl bg-muted/50 p-1">
              <TabsTrigger value="restaurants" className="gap-2 rounded-lg data-[state=active]:shadow-sm">
                <Store size={16} /> المطاعم
              </TabsTrigger>
              <TabsTrigger value="monitoring" className="gap-2 rounded-lg data-[state=active]:shadow-sm">
                <PhoneCall size={16} /> المراقبة
              </TabsTrigger>
              <TabsTrigger value="sales" className="gap-2 rounded-lg data-[state=active]:shadow-sm">
                <UserPlus size={16} /> طلبات المبيعات
              </TabsTrigger>
              <TabsTrigger value="salesteam" className="gap-2 rounded-lg data-[state=active]:shadow-sm">
                <Users size={16} /> فريق المبيعات
              </TabsTrigger>
              <TabsTrigger value="zones" className="gap-2 rounded-lg data-[state=active]:shadow-sm">
                <MapIcon size={16} /> الزونز
              </TabsTrigger>
              <TabsTrigger value="analytics" className="gap-2 rounded-lg data-[state=active]:shadow-sm">
                <BarChart3 size={16} /> تحاليل المبيعات
              </TabsTrigger>
            </TabsList>

            <TabsContent value="restaurants">
              <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
                <Card className="border-border/50">
                  <CardContent className="p-4 text-center">
                    <p className="text-2xl font-heading font-bold text-foreground">{owners.length}</p>
                    <p className="text-xs text-muted-foreground">إجمالي المطاعم</p>
                  </CardContent>
                </Card>
                <Card className="border-border/50">
                  <CardContent className="p-4 text-center">
                    <p className="text-2xl font-heading font-bold text-foreground">{owners.filter((owner) => owner.status === "active").length}</p>
                    <p className="text-xs text-muted-foreground">نشطة</p>
                  </CardContent>
                </Card>
                <Card className="border-border/50">
                  <CardContent className="p-4 text-center">
                    <p className="text-2xl font-heading font-bold text-foreground">{owners.reduce((total, owner) => total + owner.totalCalls, 0)}</p>
                    <p className="text-xs text-muted-foreground">إجمالي المكالمات</p>
                  </CardContent>
                </Card>
                <Card className="border-border/50">
                  <CardContent className="p-4 text-center">
                    <p className="text-2xl font-heading font-bold text-foreground">{owners.reduce((total, owner) => total + owner.totalOrders, 0)}</p>
                    <p className="text-xs text-muted-foreground">إجمالي الأوردرات</p>
                  </CardContent>
                </Card>
              </div>

              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-heading font-bold">المطاعم المسجلة</h2>
                <Button onClick={() => setShowRegister((current) => !current)} className="gap-2 rounded-xl bg-primary text-primary-foreground">
                  <Plus size={16} /> تسجيل مطعم جديد
                </Button>
              </div>

              {showRegister && (
                <Card className="mb-6 border-border/50">
                  <CardHeader>
                    <CardTitle className="text-base font-heading">تسجيل مطعم جديد</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div className="space-y-1">
                        <Label className="text-xs">اسم المطعم</Label>
                        <Input value={newRestaurant.name} onChange={(event) => setNewRestaurant((current) => ({ ...current, name: event.target.value }))} className="rounded-xl" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">اسم المالك</Label>
                        <Input value={newRestaurant.ownerName} onChange={(event) => setNewRestaurant((current) => ({ ...current, ownerName: event.target.value }))} className="rounded-xl" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">رقم المالك</Label>
                        <Input value={newRestaurant.ownerPhone} onChange={(event) => setNewRestaurant((current) => ({ ...current, ownerPhone: event.target.value }))} className="rounded-xl" dir="ltr" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">الموقع</Label>
                        <Input value={newRestaurant.location} onChange={(event) => setNewRestaurant((current) => ({ ...current, location: event.target.value }))} className="rounded-xl" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">رقم الهاتف المخصص</Label>
                        <Input value={newRestaurant.assignedPhone} onChange={(event) => setNewRestaurant((current) => ({ ...current, assignedPhone: event.target.value }))} className="rounded-xl" dir="ltr" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">الخطة</Label>
                        <Select value={newRestaurant.plan} onValueChange={(value) => setNewRestaurant((current) => ({ ...current, plan: value }))}>
                          <SelectTrigger className="rounded-xl">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="أساسي">أساسي</SelectItem>
                            <SelectItem value="بريميوم">بريميوم</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    </div>
                    <Button onClick={() => void handleRegister()} className="w-full rounded-xl bg-primary text-primary-foreground" disabled={submittingRestaurant}>
                      {submittingRestaurant ? <Loader2 size={16} className="animate-spin" /> : "تسجيل"}
                    </Button>
                  </CardContent>
                </Card>
              )}

              <div className="space-y-3">
                {owners.map((owner) => (
                  <motion.div key={owner.id} whileHover={{ scale: 1.005 }}>
                    <Card className="cursor-pointer border-border/50 transition-colors hover:bg-muted/30" onClick={() => setSelectedRestaurantId(owner.restaurantId)}>
                      <CardContent className="flex items-center justify-between p-5">
                        <div className="flex items-center gap-4">
                          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary">
                            <Store size={22} className="text-primary-foreground" />
                          </div>
                          <div>
                            <p className="font-heading font-semibold text-foreground">{owner.restaurantName}</p>
                            <p className="text-sm text-muted-foreground">{owner.name} • {owner.location}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-4">
                          <div className="hidden items-center gap-6 text-sm text-muted-foreground md:flex">
                            <span className="flex items-center gap-1"><PhoneCall size={14} /> {owner.totalCalls}</span>
                            <span className="flex items-center gap-1"><ShoppingCart size={14} /> {owner.totalOrders}</span>
                            <span className="flex items-center gap-1"><Users size={14} /> {owner.totalEmployees}</span>
                          </div>
                          <Badge variant="outline" className={statusColor(owner.status)}>{statusLabel(owner.status)}</Badge>
                          <Badge variant="outline" className="text-xs">{owner.plan}</Badge>
                          <ChevronLeft size={18} className="text-muted-foreground" />
                        </div>
                      </CardContent>
                    </Card>
                  </motion.div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="monitoring">
              <div className="grid gap-6 md:grid-cols-2">
                <Card className="border-border/50">
                  <CardHeader>
                    <CardTitle className="text-base font-heading">المكالمات الحية</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {overview.liveCalls.length === 0 && (
                        <div className="rounded-xl border border-border/50 bg-muted/20 p-4 text-sm text-muted-foreground">
                          مفيش مكالمات حية دلوقتي
                        </div>
                      )}
                      {overview.liveCalls.map((call, index) => (
                        <div key={`${call.phone}-${index}`} className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/30 p-3">
                          <div>
                            <p className="text-sm font-medium">{call.restaurant}</p>
                            <p className="text-xs text-muted-foreground" dir="ltr">{call.phone}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">{call.duration}</span>
                            <Badge variant="outline" className="border-emerald-200 bg-emerald-100 text-xs text-emerald-700">{call.status}</Badge>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card className="border-border/50">
                  <CardHeader>
                    <CardTitle className="text-base font-heading">آخر الأوردرات</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {overview.recentOrders.length === 0 && (
                        <div className="rounded-xl border border-border/50 bg-muted/20 p-4 text-sm text-muted-foreground">
                          مفيش أوردرات حديثة دلوقتي
                        </div>
                      )}
                      {overview.recentOrders.map((order, index) => (
                        <div key={`${order.restaurant}-${index}`} className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/30 p-3">
                          <div>
                            <p className="text-sm font-medium">{order.restaurant}</p>
                            <p className="text-xs text-muted-foreground">{order.items}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium">{order.amount}</span>
                            <Badge variant="outline" className="text-xs">{recentOrderStatusLabel(order.status)}</Badge>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            <TabsContent value="sales">
              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle className="text-base font-heading">طلبات تسجيل من فريق المبيعات</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {salesRequests.length === 0 && (
                      <div className="rounded-xl border border-border/50 bg-muted/20 p-4 text-sm text-muted-foreground">
                        مفيش طلبات حالياً
                      </div>
                    )}
                    {salesRequests.map((request) => (
                      <div key={request.id} className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/20 p-4">
                        <div>
                          <p className="font-medium text-foreground">{request.restaurantName}</p>
                          <p className="text-sm text-muted-foreground">
                            {request.ownerName} • <span dir="ltr">{request.ownerPhone}</span> • {request.location}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">مقدم من: {request.salesPerson} — {request.date}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          {request.status === "pending" ? (
                            <>
                              <Button size="sm" className="gap-1 rounded-lg text-xs" onClick={() => void handleSalesAction(request.id, "approved")} disabled={salesActionId === request.id}>
                                {salesActionId === request.id ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle size={14} />}
                                قبول
                              </Button>
                              <Button size="sm" variant="outline" className="gap-1 rounded-lg text-xs text-destructive" onClick={() => void handleSalesAction(request.id, "rejected")} disabled={salesActionId === request.id}>
                                <XCircle size={14} /> رفض
                              </Button>
                            </>
                          ) : (
                            <Badge variant="outline" className={salesRequestBadge(request.status)}>
                              {request.status === "approved" ? "مقبول" : "مرفوض"}
                            </Badge>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="salesteam">
              <Card className="mb-6 border-border/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base font-heading">
                    <UserPlus size={18} className="text-primary" />
                    إضافة شخص لفريق المبيعات
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div className="space-y-1">
                      <Label className="text-xs">الاسم</Label>
                      <Input value={newSalesName} onChange={(event) => setNewSalesName(event.target.value)} className="rounded-xl" placeholder="اسم الشخص" />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">رقم الموبايل</Label>
                      <Input value={newSalesPhone} onChange={(event) => setNewSalesPhone(event.target.value)} className="rounded-xl" dir="ltr" placeholder="+201XXXXXXXXX" />
                    </div>
                  </div>
                  <Button onClick={() => void handleAddSales()} className="w-full gap-2 rounded-xl bg-primary text-primary-foreground" disabled={submittingSales}>
                    {submittingSales ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
                    إضافة للفريق
                  </Button>
                </CardContent>
              </Card>

              <Card className="border-border/50">
                <CardHeader>
                  <CardTitle className="text-base font-heading">فريق المبيعات الحالي</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {salesTeam.length === 0 ? (
                      <p className="py-4 text-center text-sm text-muted-foreground">مفيش أعضاء في فريق المبيعات حالياً</p>
                    ) : (
                      salesTeam.map((member) => (
                        <div key={member.id} className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/20 p-4">
                          <div>
                            <p className="text-sm font-medium text-foreground">{member.name}</p>
                            <p className="text-sm text-muted-foreground" dir="ltr">{member.phone}</p>
                            <Badge variant="outline" className="mt-1 border-blue-200 bg-blue-100 text-xs text-blue-700">سيلز</Badge>
                          </div>
                          <Button size="sm" variant="outline" className="gap-1 rounded-lg text-xs text-destructive hover:bg-destructive/10" onClick={() => void handleRemoveSales(member)} disabled={removingSalesId === member.id}>
                            {removingSalesId === member.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                            حذف
                          </Button>
                        </div>
                      ))
                    )}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="zones">
              <ZoneManagementSection />
            </TabsContent>

            <TabsContent value="analytics">
              <SalesAnalyticsSection />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  );
};

export default Admin;
