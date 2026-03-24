import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Loader2, Phone, Play, Plus, Send, Store } from "lucide-react";

import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import logo from "@/assets/logo.png";
import {
  clearAuthSession,
  createDemoSession,
  fetchDemoSessions,
  fetchSalesRequests,
  submitSalesRequest,
  type DemoSessionRecord,
  type SalesRequestRecord,
} from "@/services/api";

const SalesDashboard = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [requests, setRequests] = useState<SalesRequestRecord[]>([]);
  const [demos, setDemos] = useState<DemoSessionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [submittingRequest, setSubmittingRequest] = useState(false);
  const [submittingDemo, setSubmittingDemo] = useState(false);
  const [newReq, setNewReq] = useState({ restaurantName: "", ownerName: "", ownerPhone: "", location: "" });
  const [demoPhone, setDemoPhone] = useState("");
  const [demoRestaurant, setDemoRestaurant] = useState("");

  useEffect(() => {
    let ignore = false;

    const load = async () => {
      try {
        setLoading(true);
        const [requestsData, demosData] = await Promise.all([fetchSalesRequests(), fetchDemoSessions()]);
        if (ignore) {
          return;
        }
        setRequests(requestsData);
        setDemos(demosData);
      } catch (error) {
        console.error("Failed to load sales dashboard:", error);
        if (!ignore) {
          toast({
            title: "خطأ",
            description: "مش قادرين نجيب بيانات المبيعات دلوقتي",
            variant: "destructive",
          });
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    };

    void load();
    return () => {
      ignore = true;
    };
  }, [toast]);

  const handleLogout = () => {
    clearAuthSession();
    navigate("/login", { replace: true });
  };

  const handleSubmitRequest = async () => {
    if (!newReq.restaurantName.trim() || !newReq.ownerName.trim() || !newReq.ownerPhone.trim()) {
      toast({ title: "خطأ", description: "املا كل البيانات المطلوبة", variant: "destructive" });
      return;
    }

    try {
      setSubmittingRequest(true);
      const record = await submitSalesRequest(newReq);
      setRequests((current) => [record, ...current]);
      setNewReq({ restaurantName: "", ownerName: "", ownerPhone: "", location: "" });
      toast({ title: "تم الإرسال", description: "طلب التسجيل اتبعت للأدمن للمراجعة" });
    } catch (error) {
      console.error("Failed to submit sales request:", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نبعت الطلب دلوقتي",
        variant: "destructive",
      });
    } finally {
      setSubmittingRequest(false);
    }
  };

  const handleCreateDemo = async () => {
    if (!demoPhone.trim() || !demoRestaurant.trim()) {
      toast({ title: "خطأ", description: "اكتب اسم المطعم ورقم الموبايل", variant: "destructive" });
      return;
    }

    try {
      setSubmittingDemo(true);
      const demo = await createDemoSession({ restaurantName: demoRestaurant, phoneNumber: demoPhone });
      setDemos((current) => [demo, ...current]);
      setDemoPhone("");
      setDemoRestaurant("");
      toast({ title: "تم التسجيل", description: "جلسة الديمو اتسجلت لفريق المتابعة" });
    } catch (error) {
      console.error("Failed to create demo session:", error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نسجل جلسة الديمو دلوقتي",
        variant: "destructive",
      });
    } finally {
      setSubmittingDemo(false);
    }
  };

  const statusColor = (status: string) => {
    if (status === "approved" || status === "completed") return "bg-emerald-100 text-emerald-700 border-emerald-200";
    if (status === "rejected") return "bg-destructive/10 text-destructive border-destructive/20";
    if (status === "in_progress") return "bg-blue-100 text-blue-700 border-blue-200";
    return "bg-amber-100 text-amber-700 border-amber-200";
  };

  const statusLabel = (status: string) => {
    const map: Record<string, string> = {
      pending: "في الانتظار",
      approved: "مقبول",
      rejected: "مرفوض",
      scheduled: "مجدول",
      completed: "تم",
      in_progress: "جاري",
    };
    return map[status] || status;
  };

  if (loading && requests.length === 0 && demos.length === 0) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <Loader2 size={18} className="animate-spin text-primary" />
          بنجهز لوحة المبيعات...
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="glass sticky top-0 z-40 border-b border-border">
        <div className="container mx-auto flex h-16 items-center justify-between px-4">
          <Link to="/" className="flex items-center gap-2">
            <img src={logo} alt="ألو إيچي" className="h-8 w-8 object-contain" />
            <span className="text-xl font-heading font-bold text-foreground">ألو إيچي</span>
            <Badge className="border-blue-200 bg-blue-100 text-xs text-blue-700">مبيعات</Badge>
          </Link>
          <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={handleLogout}>
            تسجيل خروج
          </Button>
        </div>
      </div>

      <div className="container mx-auto px-4 py-6">
        <Tabs defaultValue="register" className="space-y-4">
          <TabsList className="rounded-xl bg-muted/50 p-1">
            <TabsTrigger value="register" className="gap-2 rounded-lg data-[state=active]:shadow-sm">
              <Store size={16} /> طلب تسجيل مطعم
            </TabsTrigger>
            <TabsTrigger value="demo" className="gap-2 rounded-lg data-[state=active]:shadow-sm">
              <Play size={16} /> Demo Sessions
            </TabsTrigger>
          </TabsList>

          <TabsContent value="register" className="space-y-6">
            <Card className="border-border/50">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base font-heading">
                  <Plus size={18} className="text-primary" />
                  تقديم طلب تسجيل مطعم جديد
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="mb-2 text-xs text-muted-foreground">الطلب هيتبعت للأدمن للمراجعة والموافقة</p>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label className="text-xs">اسم المطعم</Label>
                    <Input value={newReq.restaurantName} onChange={(event) => setNewReq((current) => ({ ...current, restaurantName: event.target.value }))} className="rounded-xl" placeholder="مثلاً: بيتزا كينج" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">اسم المالك</Label>
                    <Input value={newReq.ownerName} onChange={(event) => setNewReq((current) => ({ ...current, ownerName: event.target.value }))} className="rounded-xl" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">رقم المالك</Label>
                    <Input value={newReq.ownerPhone} onChange={(event) => setNewReq((current) => ({ ...current, ownerPhone: event.target.value }))} className="rounded-xl" dir="ltr" placeholder="+201XXXXXXXXX" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">الموقع</Label>
                    <Input value={newReq.location} onChange={(event) => setNewReq((current) => ({ ...current, location: event.target.value }))} className="rounded-xl" placeholder="المنطقة، المدينة" />
                  </div>
                </div>
                <Button onClick={() => void handleSubmitRequest()} className="w-full gap-2 rounded-xl bg-primary text-primary-foreground" disabled={submittingRequest}>
                  {submittingRequest ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                  إرسال الطلب
                </Button>
              </CardContent>
            </Card>

            <Card className="border-border/50">
              <CardHeader>
                <CardTitle className="text-base font-heading">طلباتي السابقة</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {requests.length === 0 ? (
                    <div className="rounded-xl border border-border/50 bg-muted/20 p-4 text-sm text-muted-foreground">
                      مفيش طلبات مرسلة لسه
                    </div>
                  ) : (
                    requests.map((request) => (
                      <div key={request.id} className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/20 p-4">
                        <div>
                          <p className="font-medium text-foreground">{request.restaurantName}</p>
                          <p className="text-sm text-muted-foreground">
                            {request.ownerName} • <span dir="ltr">{request.ownerPhone}</span> • {request.location}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">{request.date}</p>
                        </div>
                        <Badge variant="outline" className={statusColor(request.status)}>
                          {statusLabel(request.status)}
                        </Badge>
                      </div>
                    ))
                  )}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="demo" className="space-y-6">
            <Card className="border-border/50">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base font-heading">
                  <Play size={18} className="text-primary" />
                  تسجيل جلسة ديمو
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="text-xs text-muted-foreground">سجل بيانات عميل محتمل عشان فريقك يتابع معاه جلسة الديمو.</p>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label className="text-xs">اسم المطعم</Label>
                    <Input value={demoRestaurant} onChange={(event) => setDemoRestaurant(event.target.value)} className="rounded-xl" placeholder="اسم المطعم" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">رقم الموبايل</Label>
                    <Input value={demoPhone} onChange={(event) => setDemoPhone(event.target.value)} className="rounded-xl" dir="ltr" placeholder="+201XXXXXXXXX" />
                  </div>
                </div>
                <Button onClick={() => void handleCreateDemo()} className="w-full gap-2 rounded-xl bg-primary text-primary-foreground" disabled={submittingDemo}>
                  {submittingDemo ? <Loader2 size={16} className="animate-spin" /> : <Phone size={16} />}
                  سجّل الديمو
                </Button>
              </CardContent>
            </Card>

            <Card className="border-border/50">
              <CardHeader>
                <CardTitle className="text-base font-heading">سجل الديموهات</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {demos.length === 0 ? (
                    <div className="rounded-xl border border-border/50 bg-muted/20 p-4 text-sm text-muted-foreground">
                      مفيش جلسات ديمو متسجلة لسه
                    </div>
                  ) : (
                    demos.map((demo) => (
                      <div key={demo.id} className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/20 p-4">
                        <div>
                          <p className="font-medium text-foreground">{demo.restaurantName}</p>
                          <p className="text-sm text-muted-foreground" dir="ltr">{demo.phoneNumber}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{demo.date}</p>
                        </div>
                        <Badge variant="outline" className={statusColor(demo.status)}>
                          {statusLabel(demo.status)}
                        </Badge>
                      </div>
                    ))
                  )}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
};

export default SalesDashboard;
