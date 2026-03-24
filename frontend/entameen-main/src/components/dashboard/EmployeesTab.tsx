import { useEffect, useState } from "react";
import { Plus, Trash2, UserCog } from "lucide-react";

import { createEmployee, deleteEmployee, fetchEmployees, type Employee } from "@/services/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";

interface EmployeesTabProps {
  restaurantId?: number;
}

const EmployeesTab = ({ restaurantId }: EmployeesTabProps) => {
  const { toast } = useToast();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [newName, setNewName] = useState("");
  const [newPhone, setNewPhone] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let ignore = false;

    const load = async () => {
      try {
        setLoading(true);
        const data = await fetchEmployees(restaurantId);
        if (!ignore) setEmployees(data);
      } catch (error) {
        console.error("Failed to load employees:", error);
        if (!ignore) {
          toast({ title: "خطأ", description: "مش قادرين نجيب الموظفين دلوقتي", variant: "destructive" });
        }
      } finally {
        if (!ignore) setLoading(false);
      }
    };

    void load();
    return () => {
      ignore = true;
    };
  }, [restaurantId, toast]);

  const addEmployeeHandler = async () => {
    if (!newName.trim() || !newPhone.trim()) {
      toast({ title: "خطأ", description: "اكتب اسم الموظف ورقم الموبايل", variant: "destructive" });
      return;
    }

    try {
      const employee = await createEmployee(newName.trim(), newPhone.trim(), restaurantId);
      setEmployees((current) => [...current, employee]);
      setNewName("");
      setNewPhone("");
      toast({ title: "تم الإضافة ✅", description: `${employee.name} اتضاف كموظف` });
    } catch (error) {
      console.error("Failed to create employee:", error);
      toast({ title: "خطأ", description: "الإضافة فشلت، راجع الرقم أو حاول تاني", variant: "destructive" });
    }
  };

  const removeEmployeeHandler = async (employee: Employee) => {
    try {
      await deleteEmployee(employee.id, restaurantId);
      setEmployees((current) => current.filter((item) => item.id !== employee.id));
      toast({ title: "تم الحذف", description: "الموظف اتشال من القائمة" });
    } catch (error) {
      console.error("Failed to delete employee:", error);
      toast({ title: "خطأ", description: "الحذف فشل، حاول تاني", variant: "destructive" });
    }
  };

  return (
    <div className="max-w-2xl space-y-6">
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base font-heading">
            <UserCog size={18} className="text-primary" />
            إضافة موظف جديد
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-3 sm:flex-row">
            <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="اسم الموظف" className="flex-1 rounded-xl" />
            <Input value={newPhone} onChange={(e) => setNewPhone(e.target.value)} placeholder="رقم الموبايل" className="flex-1 rounded-xl" dir="ltr" />
            <Button onClick={() => void addEmployeeHandler()} className="gap-2 rounded-xl bg-primary text-primary-foreground" disabled={loading}>
              <Plus size={16} /> أضف
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">الموظف هيقدر يشوف الأوردرات بس ويغير حالتها</p>
        </CardContent>
      </Card>

      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base font-heading">الموظفين الحاليين</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">جاري تحميل الموظفين...</div>
          ) : employees.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">مفيش موظفين لسه</div>
          ) : (
            <div className="space-y-3">
              {employees.map((employee) => (
                <div key={employee.id} className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/30 p-4">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent">
                      <span className="text-sm font-bold text-accent-foreground">{employee.name.charAt(0)}</span>
                    </div>
                    <div>
                      <p className="text-sm font-medium text-foreground">{employee.name}</p>
                      <p className="text-xs text-muted-foreground" dir="ltr">{employee.phone}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-xs">أوردرات فقط</Badge>
                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive/60 hover:text-destructive" onClick={() => void removeEmployeeHandler(employee)}>
                      <Trash2 size={14} />
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
};

export default EmployeesTab;
