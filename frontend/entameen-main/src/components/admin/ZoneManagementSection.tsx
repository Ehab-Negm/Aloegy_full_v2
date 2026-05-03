import { useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ClipboardPaste,
  Loader2,
  MapPin,
  Plus,
  Send,
  Store,
  Trash2,
  Users,
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
import {
  addAdminZoneRestaurant,
  bulkAddAdminZoneRestaurants,
  createAdminSalesZone,
  deleteAdminSalesZone,
  deleteAdminZoneRestaurant,
  fetchAdminSalesTeam,
  fetchAdminSalesZones,
  fetchAdminZoneAssignments,
  fetchAdminZoneRestaurants,
  setAdminZoneAssignments,
  updateAdminSalesZone,
  updateAdminZoneRestaurant,
  type RestaurantStatus,
  type SalesTeamMember,
  type SalesZone,
  type ZoneAssignmentRecord,
  type ZoneRestaurant,
} from "@/services/api";

const STATUS_LABEL: Record<RestaurantStatus, string> = {
  pending: "ما اتزرتش",
  in_progress: "في الشغل",
  won: "اتقفل",
  lost: "اترفض",
  follow_up: "متابعة",
};

const STATUS_COLOR: Record<RestaurantStatus, string> = {
  pending: "bg-amber-100 text-amber-700 border-amber-200",
  in_progress: "bg-blue-100 text-blue-700 border-blue-200",
  won: "bg-emerald-100 text-emerald-700 border-emerald-200",
  lost: "bg-destructive/10 text-destructive border-destructive/20",
  follow_up: "bg-purple-100 text-purple-700 border-purple-200",
};

const PALETTE = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899"];

const ZoneManagementSection = () => {
  const { toast } = useToast();
  const [zones, setZones] = useState<SalesZone[]>([]);
  const [salesTeam, setSalesTeam] = useState<SalesTeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeZoneId, setActiveZoneId] = useState<number | null>(null);
  const [zoneRestaurants, setZoneRestaurants] = useState<ZoneRestaurant[]>([]);
  const [zoneAssignments, setZoneAssignments] = useState<ZoneAssignmentRecord[]>([]);
  const [zoneLoading, setZoneLoading] = useState(false);

  // create-zone form
  const [newZoneName, setNewZoneName] = useState("");
  const [newZoneDesc, setNewZoneDesc] = useState("");
  const [newZoneColor, setNewZoneColor] = useState(PALETTE[0]);
  const [creatingZone, setCreatingZone] = useState(false);

  // single-restaurant add
  const [restName, setRestName] = useState("");
  const [restLocation, setRestLocation] = useState("");
  const [addingRestaurant, setAddingRestaurant] = useState(false);

  // bulk-paste
  const [bulkText, setBulkText] = useState("");
  const [bulkSubmitting, setBulkSubmitting] = useState(false);

  // assignment
  const [selectedRepIds, setSelectedRepIds] = useState<Set<number>>(new Set());
  const [savingAssignments, setSavingAssignments] = useState(false);

  useEffect(() => {
    let ignore = false;
    const load = async () => {
      try {
        setLoading(true);
        const [zonesData, teamData] = await Promise.all([
          fetchAdminSalesZones(),
          fetchAdminSalesTeam(),
        ]);
        if (ignore) return;
        setZones(zonesData);
        setSalesTeam(teamData);
      } catch (error) {
        console.error("Failed to load zones", error);
        if (!ignore) {
          toast({ title: "خطأ", description: "مش قادرين نجيب الزونز", variant: "destructive" });
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

  useEffect(() => {
    if (!activeZoneId) return;
    let ignore = false;
    const load = async () => {
      try {
        setZoneLoading(true);
        const [restaurantsData, assignmentsData] = await Promise.all([
          fetchAdminZoneRestaurants(activeZoneId),
          fetchAdminZoneAssignments(activeZoneId),
        ]);
        if (ignore) return;
        setZoneRestaurants(restaurantsData);
        setZoneAssignments(assignmentsData);
        setSelectedRepIds(new Set(assignmentsData.map((entry) => entry.repId)));
      } catch (error) {
        console.error("Failed to load zone detail", error);
        if (!ignore) {
          toast({ title: "خطأ", description: "مش قادرين نجيب تفاصيل الزون", variant: "destructive" });
        }
      } finally {
        if (!ignore) setZoneLoading(false);
      }
    };
    void load();
    return () => {
      ignore = true;
    };
  }, [activeZoneId, toast]);

  const handleCreateZone = async () => {
    if (!newZoneName.trim()) {
      toast({ title: "خطأ", description: "اكتب اسم الزون", variant: "destructive" });
      return;
    }
    try {
      setCreatingZone(true);
      const zone = await createAdminSalesZone({
        name: newZoneName.trim(),
        description: newZoneDesc.trim(),
        color: newZoneColor,
      });
      setZones((current) => [zone, ...current]);
      setNewZoneName("");
      setNewZoneDesc("");
      setNewZoneColor(PALETTE[0]);
      toast({ title: "تمام", description: `اتعمل الزون: ${zone.name}` });
    } catch (error) {
      console.error(error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نعمل الزون",
        variant: "destructive",
      });
    } finally {
      setCreatingZone(false);
    }
  };

  const handleDeleteZone = async (zone: SalesZone) => {
    if (!window.confirm(`متأكد عايز تحذف زون "${zone.name}" وكل المطاعم اللي جواه؟`)) return;
    try {
      await deleteAdminSalesZone(zone.id);
      setZones((current) => current.filter((entry) => entry.id !== zone.id));
      if (activeZoneId === zone.id) setActiveZoneId(null);
      toast({ title: "تم الحذف", description: zone.name });
    } catch (error) {
      console.error(error);
      toast({ title: "خطأ", description: "مش قادرين نحذف الزون", variant: "destructive" });
    }
  };

  const handleAddSingleRestaurant = async () => {
    if (!activeZoneId) return;
    if (!restName.trim()) {
      toast({ title: "خطأ", description: "اكتب اسم المطعم", variant: "destructive" });
      return;
    }
    try {
      setAddingRestaurant(true);
      const created = await addAdminZoneRestaurant(activeZoneId, {
        name: restName.trim(),
        location: restLocation.trim(),
      });
      setZoneRestaurants((current) => [...current, created]);
      setZones((current) =>
        current.map((entry) =>
          entry.id === activeZoneId ? { ...entry, restaurantCount: entry.restaurantCount + 1 } : entry,
        ),
      );
      setRestName("");
      setRestLocation("");
      toast({ title: "تمام", description: "اتضاف المطعم" });
    } catch (error) {
      console.error(error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نضيف المطعم",
        variant: "destructive",
      });
    } finally {
      setAddingRestaurant(false);
    }
  };

  const handleBulkAdd = async () => {
    if (!activeZoneId) return;
    if (!bulkText.trim()) {
      toast({ title: "خطأ", description: "ال patch فاضي", variant: "destructive" });
      return;
    }
    try {
      setBulkSubmitting(true);
      const result = await bulkAddAdminZoneRestaurants(activeZoneId, bulkText);
      setZoneRestaurants((current) => [...current, ...result.restaurants]);
      setZones((current) =>
        current.map((entry) =>
          entry.id === activeZoneId
            ? { ...entry, restaurantCount: entry.restaurantCount + result.added }
            : entry,
        ),
      );
      setBulkText("");
      toast({
        title: "تمام",
        description: `اتضاف ${result.added} مطعم${result.skippedDuplicates ? ` (تخطي ${result.skippedDuplicates} مكرر)` : ""}`,
      });
    } catch (error) {
      console.error(error);
      toast({
        title: "خطأ",
        description: error instanceof Error ? error.message : "مش قادرين نضيف القائمة",
        variant: "destructive",
      });
    } finally {
      setBulkSubmitting(false);
    }
  };

  const handleDeleteRestaurant = async (restaurant: ZoneRestaurant) => {
    if (!window.confirm(`متأكد عايز تحذف "${restaurant.name}"؟ كل تقاريره هتتمسح.`)) return;
    try {
      await deleteAdminZoneRestaurant(restaurant.id);
      setZoneRestaurants((current) => current.filter((entry) => entry.id !== restaurant.id));
      if (activeZoneId !== null) {
        setZones((current) =>
          current.map((entry) =>
            entry.id === activeZoneId
              ? { ...entry, restaurantCount: Math.max(0, entry.restaurantCount - 1) }
              : entry,
          ),
        );
      }
      toast({ title: "تم الحذف", description: restaurant.name });
    } catch (error) {
      console.error(error);
      toast({ title: "خطأ", description: "مش قادرين نحذف المطعم", variant: "destructive" });
    }
  };

  const handleStatusChange = async (restaurant: ZoneRestaurant, status: RestaurantStatus) => {
    if (restaurant.status === status) return;
    try {
      const updated = await updateAdminZoneRestaurant(restaurant.id, { status });
      setZoneRestaurants((current) =>
        current.map((entry) => (entry.id === updated.id ? updated : entry)),
      );
    } catch (error) {
      console.error(error);
      toast({ title: "خطأ", description: "مش قادرين نحدث الحالة", variant: "destructive" });
    }
  };

  const toggleRepSelected = (repId: number) => {
    setSelectedRepIds((current) => {
      const next = new Set(current);
      if (next.has(repId)) next.delete(repId);
      else next.add(repId);
      return next;
    });
  };

  const handleSaveAssignments = async () => {
    if (!activeZoneId) return;
    try {
      setSavingAssignments(true);
      const next = await setAdminZoneAssignments(activeZoneId, Array.from(selectedRepIds));
      setZoneAssignments(next);
      setZones((current) =>
        current.map((entry) =>
          entry.id === activeZoneId ? { ...entry, repCount: next.length } : entry,
        ),
      );
      toast({ title: "تمام", description: `${next.length} مندوب مخصص للزون` });
    } catch (error) {
      console.error(error);
      toast({ title: "خطأ", description: "مش قادرين نحفظ التخصيصات", variant: "destructive" });
    } finally {
      setSavingAssignments(false);
    }
  };

  if (loading && zones.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-border/50 bg-muted/20 p-8 text-sm text-muted-foreground">
        <Loader2 size={16} className="ml-2 animate-spin text-primary" />
        بنحمل الزونز...
      </div>
    );
  }

  if (activeZone) {
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" className="gap-1 rounded-lg" onClick={() => setActiveZoneId(null)}>
              <ChevronLeft size={14} />
              رجوع
            </Button>
            <h2 className="font-heading text-lg font-bold flex items-center gap-2">
              <span className="inline-block h-3 w-3 rounded-full" style={{ backgroundColor: activeZone.color }} />
              {activeZone.name}
            </h2>
            <Badge variant="outline" className="bg-muted/40 text-xs">
              {zoneRestaurants.length} مطعم
            </Badge>
            <Badge variant="outline" className="bg-muted/40 text-xs">
              {zoneAssignments.length} مندوب
            </Badge>
          </div>
          <Button
            size="sm"
            variant="ghost"
            className="gap-1 rounded-lg text-destructive hover:bg-destructive/10"
            onClick={() => void handleDeleteZone(activeZone)}
          >
            <Trash2 size={14} />
            حذف الزون
          </Button>
        </div>

        {/* Assignments */}
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Users size={16} className="text-primary" />
              المندوبين المسؤولين
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {salesTeam.length === 0 ? (
              <p className="rounded-lg bg-muted/20 p-3 text-xs text-muted-foreground">
                مفيش مندوبين متسجلين. ضيف مندوبين من تاب "فريق المبيعات" الأول.
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {salesTeam.map((member) => (
                  <label
                    key={member.id}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border p-3 text-sm transition ${
                      selectedRepIds.has(member.id)
                        ? "border-primary/40 bg-primary/5"
                        : "border-border/50 bg-card hover:bg-muted/20"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4"
                      checked={selectedRepIds.has(member.id)}
                      onChange={() => toggleRepSelected(member.id)}
                    />
                    <div className="flex-1">
                      <p className="font-medium text-foreground">{member.name}</p>
                      <p className="text-xs text-muted-foreground" dir="ltr">
                        {member.phone}
                      </p>
                    </div>
                  </label>
                ))}
              </div>
            )}
            <Button
              onClick={() => void handleSaveAssignments()}
              disabled={savingAssignments}
              className="w-full gap-2 rounded-xl bg-primary text-primary-foreground"
            >
              {savingAssignments ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
              احفظ التخصيصات
            </Button>
          </CardContent>
        </Card>

        {/* Bulk paste */}
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <ClipboardPaste size={16} className="text-primary" />
              إضافة مطاعم بالجملة (paste list)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              سطر واحد لكل مطعم. الفاصل بين الاسم والمنطقة: <span className="font-mono">—</span> أو{" "}
              <span className="font-mono">|</span> أو <span className="font-mono">,</span> أو{" "}
              <span className="font-mono">:</span>. لو كتبت اسم بس بدون فاصل، هيتسجل بدون منطقة.
            </p>
            <Textarea
              value={bulkText}
              onChange={(event) => setBulkText(event.target.value)}
              placeholder={"بيتزا كينج — المهندسين\nكشري التحرير | وسط البلد\nماك دونالدز, الزمالك\nبرجر كنج"}
              className="min-h-[160px] rounded-xl font-mono text-sm"
            />
            <Button
              onClick={() => void handleBulkAdd()}
              disabled={bulkSubmitting}
              className="w-full gap-2 rounded-xl bg-primary text-primary-foreground"
            >
              {bulkSubmitting ? <Loader2 size={14} className="animate-spin" /> : <ClipboardPaste size={14} />}
              ضيف القائمة
            </Button>
          </CardContent>
        </Card>

        {/* Single add */}
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Plus size={16} className="text-primary" />
              إضافة مطعم فردي
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Input
                value={restName}
                onChange={(event) => setRestName(event.target.value)}
                placeholder="اسم المطعم"
                className="rounded-xl"
              />
              <Input
                value={restLocation}
                onChange={(event) => setRestLocation(event.target.value)}
                placeholder="العنوان / المنطقة"
                className="rounded-xl"
              />
            </div>
            <Button
              onClick={() => void handleAddSingleRestaurant()}
              disabled={addingRestaurant}
              variant="outline"
              className="w-full gap-2 rounded-xl"
            >
              {addingRestaurant ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              ضيف مطعم
            </Button>
          </CardContent>
        </Card>

        {/* Restaurants list */}
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base font-heading">
              <Store size={16} className="text-primary" />
              مطاعم الزون ({zoneRestaurants.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            {zoneLoading ? (
              <div className="flex items-center justify-center text-xs text-muted-foreground">
                <Loader2 size={14} className="ml-2 animate-spin text-primary" />
                بنحمل المطاعم...
              </div>
            ) : zoneRestaurants.length === 0 ? (
              <p className="rounded-xl border border-border/50 bg-muted/20 p-4 text-center text-sm text-muted-foreground">
                مفيش مطاعم في الزون لسه
              </p>
            ) : (
              <div className="space-y-2">
                {zoneRestaurants.map((restaurant) => (
                  <div
                    key={restaurant.id}
                    className="flex flex-col gap-2 rounded-xl border border-border/50 bg-card p-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium text-foreground">{restaurant.name}</p>
                        <Badge variant="outline" className={STATUS_COLOR[restaurant.status]}>
                          {STATUS_LABEL[restaurant.status]}
                        </Badge>
                        {restaurant.reportsCount > 0 ? (
                          <Badge variant="outline" className="bg-muted/40 text-[11px]">
                            {restaurant.reportsCount} زيارة
                          </Badge>
                        ) : null}
                      </div>
                      {restaurant.location ? (
                        <p className="text-xs text-muted-foreground">
                          <MapPin size={11} className="ml-1 inline" />
                          {restaurant.location}
                        </p>
                      ) : null}
                      {restaurant.lastReportPreview ? (
                        <p className="line-clamp-1 text-xs text-muted-foreground">
                          آخر زيارة: {restaurant.lastReportPreview}
                        </p>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Select
                        value={restaurant.status}
                        onValueChange={(value) => void handleStatusChange(restaurant, value as RestaurantStatus)}
                      >
                        <SelectTrigger className="h-8 w-32 rounded-lg text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {Object.entries(STATUS_LABEL).map(([key, label]) => (
                            <SelectItem key={key} value={key}>
                              {label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-8 gap-1 rounded-lg text-xs text-destructive hover:bg-destructive/10"
                        onClick={() => void handleDeleteRestaurant(restaurant)}
                      >
                        <Trash2 size={12} />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  // Zone list (default)
  return (
    <div className="space-y-6">
      {/* Create zone */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <Plus size={18} className="text-primary" />
            إنشاء زون جديد
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-xs">اسم الزون</Label>
              <Input
                value={newZoneName}
                onChange={(event) => setNewZoneName(event.target.value)}
                placeholder="مثلاً: وسط البلد"
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">لون الزون</Label>
              <div className="flex flex-wrap gap-2 pt-1">
                {PALETTE.map((color) => (
                  <button
                    key={color}
                    type="button"
                    onClick={() => setNewZoneColor(color)}
                    className={`h-7 w-7 rounded-full border-2 transition ${
                      newZoneColor === color ? "border-foreground scale-110" : "border-transparent"
                    }`}
                    style={{ backgroundColor: color }}
                  />
                ))}
              </div>
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">وصف (اختياري)</Label>
            <Textarea
              value={newZoneDesc}
              onChange={(event) => setNewZoneDesc(event.target.value)}
              placeholder="معلومات عن الزون، الأهداف، إلخ..."
              className="min-h-[60px] rounded-xl"
            />
          </div>
          <Button
            onClick={() => void handleCreateZone()}
            disabled={creatingZone}
            className="w-full gap-2 rounded-xl bg-primary text-primary-foreground"
          >
            {creatingZone ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            اعمل الزون
          </Button>
        </CardContent>
      </Card>

      {/* Zone list */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base font-heading">الزونز الحالية</CardTitle>
        </CardHeader>
        <CardContent>
          {zones.length === 0 ? (
            <p className="rounded-xl border border-border/50 bg-muted/20 p-4 text-center text-sm text-muted-foreground">
              لسه مفيش زونز. اعمل واحد من الفورم فوق.
            </p>
          ) : (
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
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2 pt-0">
                    {zone.description ? (
                      <p className="line-clamp-2 text-xs text-foreground/70">{zone.description}</p>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline" className="bg-muted/30 text-[11px]">
                        <Store size={11} className="ml-1 inline" />
                        {zone.restaurantCount} مطعم
                      </Badge>
                      <Badge variant="outline" className="bg-muted/30 text-[11px]">
                        <Users size={11} className="ml-1 inline" />
                        {zone.repCount} مندوب
                      </Badge>
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
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default ZoneManagementSection;
