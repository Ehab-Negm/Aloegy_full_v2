import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, PhoneCall, Search, ShieldCheck } from "lucide-react";
import { motion } from "framer-motion";

import { useToast } from "@/hooks/use-toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { type Call, updateCallReview } from "@/services/api";

const callStatusBadge = (status: string) => {
  const map: Record<string, { label: string; className: string }> = {
    active: { label: "جارية", className: "bg-emerald-100 text-emerald-700 border-emerald-200" },
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

const reviewStatusBadge = (status: string) => {
  const map: Record<string, { label: string; className: string }> = {
    needs_review: { label: "محتاجة مراجعة", className: "bg-amber-100 text-amber-700 border-amber-200" },
    reviewed: { label: "متراجعة", className: "bg-emerald-100 text-emerald-700 border-emerald-200" },
    ignored: { label: "متخطية", className: "bg-slate-100 text-slate-700 border-slate-200" },
  };
  const state = map[status] || map.needs_review;
  return (
    <Badge variant="outline" className={state.className}>
      {state.label}
    </Badge>
  );
};

const outcomeBadge = (outcome: string) => {
  const map: Record<string, { label: string; className: string }> = {
    order_confirmed: { label: "طلب متقفل", className: "bg-emerald-100 text-emerald-700 border-emerald-200" },
    reservation_confirmed: { label: "حجز متقفل", className: "bg-sky-100 text-sky-700 border-sky-200" },
    complaint_logged: { label: "شكوى متسجلة", className: "bg-violet-100 text-violet-700 border-violet-200" },
    handoff: { label: "تحويل", className: "bg-blue-100 text-blue-700 border-blue-200" },
    abandoned: { label: "العميل سابها", className: "bg-amber-100 text-amber-700 border-amber-200" },
    failed: { label: "فشلت", className: "bg-rose-100 text-rose-700 border-rose-200" },
    closed_without_action: { label: "مفيش إجراء", className: "bg-slate-100 text-slate-700 border-slate-200" },
  };
  const state = map[outcome] || map.closed_without_action;
  return (
    <Badge variant="outline" className={state.className}>
      {state.label}
    </Badge>
  );
};

const FLOW_LABELS: Record<string, string> = {
  greeter: "الاستقبال",
  takeaway: "تيك أواي",
  delivery: "ديليفري",
  reservation: "حجز",
  complaint: "شكوى",
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

interface CallsTabProps {
  calls: Call[];
  readOnly?: boolean;
  onCallUpdated: (call: Call) => void;
}

const CallsTab = ({ calls, readOnly, onCallUpdated }: CallsTabProps) => {
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [selectedCallId, setSelectedCallId] = useState<number | null>(null);
  const [reviewStatus, setReviewStatus] = useState<Call["reviewStatus"]>("needs_review");
  const [failureReason, setFailureReason] = useState("");
  const [reviewNotes, setReviewNotes] = useState("");
  const [saving, setSaving] = useState(false);

  const filteredCalls = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) {
      return calls;
    }
    return calls.filter((call) =>
      [
        call.phone,
        call.customerName,
        call.callId,
        call.outcome,
        call.failureReason,
        call.reviewStatus,
        call.flow,
      ]
        .join(" ")
        .toLowerCase()
        .includes(query),
    );
  }, [calls, search]);

  const selectedCall = filteredCalls.find((call) => call.id === selectedCallId)
    ?? calls.find((call) => call.id === selectedCallId)
    ?? null;

  useEffect(() => {
    if (!filteredCalls.length) {
      setSelectedCallId(null);
      return;
    }
    if (!selectedCallId || !filteredCalls.some((call) => call.id === selectedCallId)) {
      setSelectedCallId(filteredCalls[0].id);
    }
  }, [filteredCalls, selectedCallId]);

  useEffect(() => {
    if (!selectedCall) {
      return;
    }
    setReviewStatus(selectedCall.reviewStatus);
    setFailureReason(selectedCall.failureReason ?? "");
    setReviewNotes(selectedCall.reviewNotes ?? "");
  }, [selectedCall]);

  const handleSave = async () => {
    if (!selectedCall || readOnly) {
      return;
    }
    try {
      setSaving(true);
      const updated = await updateCallReview(selectedCall.id, {
        reviewStatus,
        failureReason,
        reviewNotes,
      });
      onCallUpdated(updated);
      toast({
        title: "تم الحفظ",
        description: "مراجعة المكالمة اتحفظت بنجاح",
      });
    } catch (error) {
      console.error("Failed to update call review:", error);
      toast({
        title: "خطأ",
        description: "مقدرتش أحفظ مراجعة المكالمة دلوقتي",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <div className="space-y-3 md:col-span-1">
        <div className="relative">
          <Search size={16} className="absolute start-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="ابحث برقم أو outcome أو reason..."
            className="rounded-xl border-border bg-muted/50 ps-10"
          />
        </div>

        {filteredCalls.map((call) => (
          <motion.button
            key={call.id}
            type="button"
            whileHover={{ scale: 1.01 }}
            onClick={() => setSelectedCallId(call.id)}
            className={`w-full rounded-xl border p-4 text-start transition-colors ${
              selectedCall?.id === call.id ? "border-primary/40 bg-accent" : "border-border/50 bg-card hover:bg-muted/30"
            }`}
          >
            <div className="mb-2 flex items-start justify-between gap-2">
              <div>
                <p className="text-sm font-medium text-foreground" dir="ltr">
                  {call.phone || "غير معروف"}
                </p>
                <p className="text-xs text-muted-foreground">{call.customerName || "بدون اسم"}</p>
              </div>
              {callStatusBadge(call.status)}
            </div>

            <div className="mb-2 flex flex-wrap gap-2">
              {outcomeBadge(call.outcome)}
              {reviewStatusBadge(call.reviewStatus)}
            </div>

            <p className="line-clamp-2 text-xs text-muted-foreground">
              {call.lastMessage || call.transcriptExcerpt || "مفيش transcript واضح للمكالمة دي"}
            </p>

            <div className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground">
              <span>{FLOW_LABELS[call.flow] || call.flow || "غير محدد"}</span>
              <span>{call.time}</span>
            </div>
          </motion.button>
        ))}

        {filteredCalls.length === 0 && (
          <Card className="border-border/50">
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              مفيش مكالمات مطابقة للبحث
            </CardContent>
          </Card>
        )}
      </div>

      <div className="md:col-span-2">
        {selectedCall ? (
          <Card className="border-border/50">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-base font-heading">مراجعة المكالمة</CardTitle>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {selectedCall.callId} • {selectedCall.customerName || "بدون اسم"}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {outcomeBadge(selectedCall.outcome)}
                  {reviewStatusBadge(selectedCall.reviewStatus)}
                </div>
              </div>
            </CardHeader>

            <CardContent className="space-y-4">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {[
                  { icon: PhoneCall, label: "رقم العميل", value: selectedCall.phone || "غير معروف", ltr: true },
                  { icon: ShieldCheck, label: "الفلو", value: FLOW_LABELS[selectedCall.flow] || selectedCall.flow || "غير محدد" },
                  { icon: CheckCircle2, label: "مدة المكالمة", value: selectedCall.duration || "00:00" },
                  {
                    icon: AlertTriangle,
                    label: "سبب الإغلاق",
                    value: FAILURE_REASON_LABELS[selectedCall.failureReason] || selectedCall.closeReason || "—",
                  },
                ].map((item) => (
                  <div key={item.label} className="rounded-xl border border-border/50 bg-muted/20 p-3">
                    <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                      <item.icon size={14} />
                      <span>{item.label}</span>
                    </div>
                    <p className="text-sm font-medium text-foreground" dir={item.ltr ? "ltr" : undefined}>
                      {item.value}
                    </p>
                  </div>
                ))}
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <div className="rounded-2xl rounded-tr-sm bg-muted px-4 py-3">
                  <p className="mb-1 text-[11px] text-muted-foreground">آخر كلام من العميل</p>
                  <p className="text-sm text-foreground">
                    {selectedCall.transcriptExcerpt || selectedCall.lastMessage || "مفيش transcript محفوظ"}
                  </p>
                </div>

                <div className="rounded-2xl rounded-tl-sm bg-primary px-4 py-3">
                  <p className="mb-1 text-[11px] text-primary-foreground/70">آخر رد من الـ agent</p>
                  <p className="text-sm text-primary-foreground">
                    {selectedCall.agentReplyExcerpt || selectedCall.aiResponse || "مفيش رد محفوظ"}
                  </p>
                </div>
              </div>

              <div className="rounded-xl border border-border/50 bg-card p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-foreground">نتيجة المراجعة</p>
                    <p className="text-xs text-muted-foreground">
                      عدّل الـ review status والسبب والملاحظات عشان نعرف نصلح الفلو ده.
                    </p>
                  </div>
                  {!readOnly && (
                    <Button onClick={() => void handleSave()} disabled={saving} className="rounded-xl">
                      {saving ? <Loader2 size={14} className="animate-spin" /> : "حفظ المراجعة"}
                    </Button>
                  )}
                </div>

                <div className="grid gap-4 lg:grid-cols-2">
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">Review status</label>
                    <Select
                      value={reviewStatus}
                      onValueChange={(value) => setReviewStatus(value as Call["reviewStatus"])}
                      disabled={Boolean(readOnly || saving)}
                    >
                      <SelectTrigger className="rounded-xl">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="needs_review">محتاجة مراجعة</SelectItem>
                        <SelectItem value="reviewed">متراجعة</SelectItem>
                        <SelectItem value="ignored">متخطية</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">Failure reason</label>
                    <Input
                      value={failureReason}
                      onChange={(event) => setFailureReason(event.target.value)}
                      placeholder="مثال: missing_phone أو customer_inactive"
                      className="rounded-xl"
                      disabled={Boolean(readOnly || saving)}
                    />
                    {selectedCall.failureReason && (
                      <p className="text-[11px] text-muted-foreground">
                        الترجمة الحالية: {FAILURE_REASON_LABELS[selectedCall.failureReason] || selectedCall.failureReason}
                      </p>
                    )}
                  </div>
                </div>

                <div className="mt-4 space-y-2">
                  <label className="text-xs font-medium text-muted-foreground">Review notes</label>
                  <Textarea
                    value={reviewNotes}
                    onChange={(event) => setReviewNotes(event.target.value)}
                    placeholder="اكتب بسرعة إيه اللي حصل وإيه المطلوب يتظبط"
                    className="min-h-[120px] rounded-xl"
                    disabled={Boolean(readOnly || saving)}
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card className="flex min-h-[300px] items-center justify-center border-border/50">
            <div className="text-center text-muted-foreground">
              <PhoneCall size={48} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">اختار مكالمة عشان تراجعها</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
};

export default CallsTab;
