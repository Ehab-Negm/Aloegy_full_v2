import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ArrowRight, Users, MapPin, ShoppingCart, Phone } from "lucide-react";
import type { UserProfile, Order } from "@/services/api";

interface CustomersTabProps {
  users: UserProfile[];
  orders: Order[];
}

const CustomersTab = ({ users, orders }: CustomersTabProps) => {
  const [selectedUser, setSelectedUser] = useState<UserProfile | null>(null);

  if (selectedUser) {
    const userOrders = orders.filter(o => o.phone === selectedUser.phone);
    return (
      <div className="space-y-4">
        <Button variant="ghost" onClick={() => setSelectedUser(null)} className="gap-2 text-muted-foreground">
          <ArrowRight size={16} /> رجوع للعملاء
        </Button>

        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-heading">بيانات العميل</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <div className="text-center p-3 rounded-xl bg-muted/30">
                <p className="text-xl font-heading font-bold text-foreground">{selectedUser.totalOrders}</p>
                <p className="text-xs text-muted-foreground">إجمالي الأوردرات</p>
              </div>
              <div className="text-center p-3 rounded-xl bg-muted/30">
                <p className="text-xl font-heading font-bold text-primary">{selectedUser.totalSpent}</p>
                <p className="text-xs text-muted-foreground">إجمالي الإنفاق</p>
              </div>
              <div className="text-center p-3 rounded-xl bg-muted/30">
                <p className="text-xl font-heading font-bold text-foreground">{selectedUser.avgOrder}</p>
                <p className="text-xs text-muted-foreground">متوسط الأوردر</p>
              </div>
              <div className="text-center p-3 rounded-xl bg-muted/30">
                <p className="text-xl font-heading font-bold text-foreground">{selectedUser.lastOrder}</p>
                <p className="text-xs text-muted-foreground">آخر أوردر</p>
              </div>
            </div>

            <div className="flex items-center gap-4 mb-6 p-4 rounded-xl bg-muted/20 border border-border/50">
              <div className="w-12 h-12 rounded-full bg-accent flex items-center justify-center">
                <Phone size={20} className="text-primary" />
              </div>
              <div>
                <p className="font-mono text-sm font-medium" dir="ltr">{selectedUser.phone}</p>
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <MapPin size={12} /> موقع تقديري: الجيزة
                </p>
              </div>
            </div>

            <h3 className="font-heading font-semibold text-sm mb-3">سجل الأوردرات</h3>
            {userOrders.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm" dir="rtl">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="py-2 px-3 text-start font-medium text-muted-foreground text-xs">رقم</th>
                      <th className="py-2 px-3 text-start font-medium text-muted-foreground text-xs">الأصناف</th>
                      <th className="py-2 px-3 text-start font-medium text-muted-foreground text-xs">المبلغ</th>
                      <th className="py-2 px-3 text-start font-medium text-muted-foreground text-xs">الحالة</th>
                      <th className="py-2 px-3 text-start font-medium text-muted-foreground text-xs">التاريخ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {userOrders.map(o => (
                      <tr key={o.id} className="border-b border-border/50">
                        <td className="py-2 px-3 font-mono text-xs" dir="ltr">{o.id}</td>
                        <td className="py-2 px-3 text-xs">{o.items}</td>
                        <td className="py-2 px-3 text-xs font-medium">{o.amount}</td>
                        <td className="py-2 px-3">
                          <Badge variant="outline" className="text-xs">{o.status}</Badge>
                        </td>
                        <td className="py-2 px-3 text-xs text-muted-foreground" dir="ltr">{o.date}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">مفيش أوردرات لسه</p>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <Card className="border-border/50">
      <CardHeader>
        <CardTitle className="text-base font-heading flex items-center gap-2">
          <Users size={18} className="text-primary" />
          سجل العملاء
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" dir="rtl">
            <thead>
              <tr className="border-b border-border">
                <th className="py-3 px-4 text-start font-medium text-muted-foreground">رقم الموبايل</th>
                <th className="py-3 px-4 text-start font-medium text-muted-foreground">عدد الأوردرات</th>
                <th className="py-3 px-4 text-start font-medium text-muted-foreground">إجمالي الإنفاق</th>
                <th className="py-3 px-4 text-start font-medium text-muted-foreground">متوسط الأوردر</th>
                <th className="py-3 px-4 text-start font-medium text-muted-foreground">آخر أوردر</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr
                  key={user.phone}
                  className="border-b border-border/50 hover:bg-muted/30 transition-colors cursor-pointer"
                  onClick={() => setSelectedUser(user)}
                >
                  <td className="py-3 px-4 font-mono text-sm font-medium" dir="ltr">{user.phone}</td>
                  <td className="py-3 px-4">{user.totalOrders}</td>
                  <td className="py-3 px-4 font-medium text-primary">{user.totalSpent}</td>
                  <td className="py-3 px-4 text-muted-foreground">{user.avgOrder}</td>
                  <td className="py-3 px-4 text-muted-foreground text-xs">{user.lastOrder}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
};

export default CustomersTab;
