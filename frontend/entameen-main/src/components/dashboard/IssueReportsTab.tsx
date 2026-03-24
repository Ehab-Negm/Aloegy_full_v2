import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle, Phone } from "lucide-react";

import { fetchIssues, updateIssueStatus, type Issue } from "@/services/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface IssueReportsTabProps {
  restaurantId?: number;
  readOnly?: boolean;
}

const IssueReportsTab = ({ restaurantId, readOnly }: IssueReportsTabProps) => {
  const [issues, setIssues] = useState<Issue[]>([]);
  const [filter, setFilter] = useState<"all" | "new" | "resolved">("all");

  useEffect(() => {
    let ignore = false;

    const load = async () => {
      try {
        const data = await fetchIssues(restaurantId);
        if (!ignore) {
          setIssues(data);
        }
      } catch (error) {
        console.error("Failed to load issues:", error);
      }
    };

    void load();
    return () => {
      ignore = true;
    };
  }, [restaurantId]);

  const filtered = useMemo(
    () => (filter === "all" ? issues : issues.filter((issue) => issue.status === filter)),
    [filter, issues],
  );

  const toggleStatus = async (issue: Issue) => {
    const nextStatus = issue.status === "new" ? "resolved" : "new";
    try {
      const updated = await updateIssueStatus(issue.id, nextStatus, restaurantId);
      setIssues((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (error) {
      console.error("Failed to update issue:", error);
    }
  };

  return (
    <Card className="border-border/50">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2 text-base font-heading">
          <AlertTriangle size={18} className="text-primary" />
          بلاغات العملاء
        </CardTitle>
        <div className="flex gap-2">
          {(["all", "new", "resolved"] as const).map((value) => (
            <Button
              key={value}
              variant={filter === value ? "default" : "outline"}
              size="sm"
              className="rounded-lg text-xs"
              onClick={() => setFilter(value)}
            >
              {value === "all" ? "الكل" : value === "new" ? "جديد" : "تم الحل"}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        <p className="mb-4 text-xs text-muted-foreground">
          لما عميل يبلغ عن مشكلة، لازم موظف يتواصل معاه بالتليفون ويتابع الحل.
        </p>
        <div className="space-y-3">
          {filtered.map((issue) => (
            <div
              key={issue.id}
              className={`rounded-xl border p-4 transition-colors ${
                issue.status === "new" ? "border-amber-200 bg-amber-50/50" : "border-border/50 bg-muted/20"
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="font-mono text-sm font-medium" dir="ltr">
                      {issue.customerPhone}
                    </span>
                    <Badge variant="outline" className="text-xs">
                      {issue.callId}
                    </Badge>
                    <Badge
                      variant="outline"
                      className={
                        issue.status === "new"
                          ? "border-amber-200 bg-amber-100 text-amber-700"
                          : "border-emerald-200 bg-emerald-100 text-emerald-700"
                      }
                    >
                      {issue.status === "new" ? "جديد" : "تم الحل"}
                    </Badge>
                  </div>
                  <p className="text-sm text-foreground">{issue.description}</p>
                  <p className="mt-1 text-xs text-muted-foreground" dir="ltr">
                    {issue.date}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1 rounded-lg text-xs"
                    onClick={() => window.open(`https://wa.me/${issue.customerPhone.replace("+", "")}`, "_blank")}
                  >
                    <Phone size={12} /> اتصل
                  </Button>
                  <Button
                    variant={issue.status === "new" ? "default" : "outline"}
                    size="sm"
                    className="gap-1 rounded-lg text-xs"
                    onClick={() => void toggleStatus(issue)}
                    disabled={Boolean(readOnly)}
                  >
                    <CheckCircle size={12} />
                    {issue.status === "new" ? "تم الحل" : "إعادة فتح"}
                  </Button>
                </div>
              </div>
            </div>
          ))}
          {filtered.length === 0 && <div className="py-8 text-center text-sm text-muted-foreground">مفيش بلاغات</div>}
        </div>
      </CardContent>
    </Card>
  );
};

export default IssueReportsTab;
