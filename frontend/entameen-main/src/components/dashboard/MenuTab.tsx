import { useEffect, useMemo, useState } from "react";
import { Plus, Save, Trash2, UtensilsCrossed } from "lucide-react";

import { createMenuItem, deleteMenuItem, fetchMenuItems, updateMenuItem, type MenuItem } from "@/services/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

const CATEGORIES = ["وجبات", "مشروبات", "حلويات", "مقبلات", "سلطات", "إضافات"];

interface MenuTabProps {
  restaurantId?: number;
  readOnly?: boolean;
}

const MenuTab = ({ restaurantId, readOnly }: MenuTabProps) => {
  const [items, setItems] = useState<MenuItem[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [savingId, setSavingId] = useState<number | null>(null);

  useEffect(() => {
    let ignore = false;

    const load = async () => {
      try {
        const data = await fetchMenuItems(restaurantId);
        if (!ignore) {
          setItems(data);
        }
      } catch (error) {
        console.error("Failed to load menu:", error);
      }
    };

    void load();
    return () => {
      ignore = true;
    };
  }, [restaurantId]);

  const addNewItem = async () => {
    try {
      const item = await createMenuItem(
        { name: "", category: "وجبات", smallPrice: "0", mediumPrice: "0", largePrice: "0", ingredients: "" },
        restaurantId,
      );
      setItems((current) => [...current, item]);
      setEditingId(item.id);
    } catch (error) {
      console.error("Failed to create menu item:", error);
    }
  };

  const updateLocalItem = (id: number, field: keyof MenuItem, value: string | boolean) => {
    setItems((current) => current.map((item) => (item.id === id ? { ...item, [field]: value } : item)));
  };

  const saveItem = async (item: MenuItem) => {
    try {
      setSavingId(item.id);
      const updated = await updateMenuItem(item.id, item, restaurantId);
      setItems((current) => current.map((entry) => (entry.id === item.id ? updated : entry)));
      setEditingId(null);
    } catch (error) {
      console.error("Failed to save menu item:", error);
    } finally {
      setSavingId(null);
    }
  };

  const deleteItemHandler = async (id: number) => {
    try {
      await deleteMenuItem(id, restaurantId);
      setItems((current) => current.filter((item) => item.id !== id));
      if (editingId === id) {
        setEditingId(null);
      }
    } catch (error) {
      console.error("Failed to delete menu item:", error);
    }
  };

  const filteredItems = useMemo(
    () => (filterCategory === "all" ? items : items.filter((item) => item.category === filterCategory)),
    [filterCategory, items],
  );
  const disabled = Boolean(readOnly);

  return (
    <Card className="border-border/50">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2 text-base font-heading">
          <UtensilsCrossed size={18} className="text-primary" />
          المنيو
        </CardTitle>
        <div className="flex items-center gap-2">
          <Select value={filterCategory} onValueChange={setFilterCategory}>
            <SelectTrigger className="h-9 w-32 rounded-lg text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">كل الفئات</SelectItem>
              {CATEGORIES.map((category) => (
                <SelectItem key={category} value={category}>
                  {category}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            onClick={() => void addNewItem()}
            size="sm"
            className="gap-2 rounded-xl bg-primary text-primary-foreground"
            disabled={disabled}
          >
            <Plus size={16} /> أضف صنف
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" dir="rtl">
            <thead>
              <tr className="border-b border-border">
                <th className="px-3 py-3 text-start font-medium text-muted-foreground">اسم الصنف</th>
                <th className="px-3 py-3 text-start font-medium text-muted-foreground">الفئة</th>
                <th className="px-3 py-3 text-start font-medium text-muted-foreground">صغير</th>
                <th className="px-3 py-3 text-start font-medium text-muted-foreground">وسط</th>
                <th className="px-3 py-3 text-start font-medium text-muted-foreground">كبير</th>
                <th className="px-3 py-3 text-start font-medium text-muted-foreground">المكونات</th>
                <th className="px-3 py-3 text-start font-medium text-muted-foreground"></th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((item) => (
                <tr
                  key={item.id}
                  className="border-b border-border/50 transition-colors hover:bg-muted/30"
                  onClick={() => {
                    if (!disabled) {
                      setEditingId(item.id);
                    }
                  }}
                >
                  <td className="px-3 py-2">
                    {editingId === item.id ? (
                      <Input
                        value={item.name}
                        onChange={(event) => updateLocalItem(item.id, "name", event.target.value)}
                        className="h-8 text-sm"
                        placeholder="اسم الصنف"
                        disabled={disabled}
                      />
                    ) : (
                      <span className="font-medium">{item.name || "—"}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {editingId === item.id ? (
                      <Select
                        value={item.category}
                        onValueChange={(value) => updateLocalItem(item.id, "category", value)}
                        disabled={disabled}
                      >
                        <SelectTrigger className="h-8 w-24 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {CATEGORIES.map((category) => (
                            <SelectItem key={category} value={category}>
                              {category}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Badge variant="outline" className="text-xs">
                        {item.category}
                      </Badge>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {editingId === item.id ? (
                      <Input
                        value={item.smallPrice}
                        onChange={(event) => updateLocalItem(item.id, "smallPrice", event.target.value)}
                        className="h-8 w-20 text-sm"
                        placeholder="السعر"
                        disabled={disabled}
                      />
                    ) : (
                      <span className="text-muted-foreground">{item.smallPrice ? `${item.smallPrice} ج.م` : "—"}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {editingId === item.id ? (
                      <Input
                        value={item.mediumPrice}
                        onChange={(event) => updateLocalItem(item.id, "mediumPrice", event.target.value)}
                        className="h-8 w-20 text-sm"
                        placeholder="السعر"
                        disabled={disabled}
                      />
                    ) : (
                      <span className="text-muted-foreground">{item.mediumPrice ? `${item.mediumPrice} ج.م` : "—"}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {editingId === item.id ? (
                      <Input
                        value={item.largePrice}
                        onChange={(event) => updateLocalItem(item.id, "largePrice", event.target.value)}
                        className="h-8 w-20 text-sm"
                        placeholder="السعر"
                        disabled={disabled}
                      />
                    ) : (
                      <span className="text-muted-foreground">{item.largePrice ? `${item.largePrice} ج.م` : "—"}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {editingId === item.id ? (
                      <Input
                        value={item.ingredients}
                        onChange={(event) => updateLocalItem(item.id, "ingredients", event.target.value)}
                        className="h-8 text-sm"
                        placeholder="المكونات"
                        disabled={disabled}
                      />
                    ) : (
                      <span className="text-xs text-muted-foreground">{item.ingredients || "—"}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1">
                      {editingId === item.id && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-primary hover:text-primary"
                          onClick={(event) => {
                            event.stopPropagation();
                            void saveItem(item);
                          }}
                          disabled={disabled || savingId === item.id}
                        >
                          <Save size={14} />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-destructive/60 hover:text-destructive"
                        onClick={(event) => {
                          event.stopPropagation();
                          void deleteItemHandler(item.id);
                        }}
                        disabled={disabled}
                      >
                        <Trash2 size={14} />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filteredItems.length === 0 && (
            <div className="py-8 text-center text-sm text-muted-foreground">مفيش أصناف في الفئة دي</div>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

export default MenuTab;
