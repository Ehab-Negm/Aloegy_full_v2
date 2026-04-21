import { useEffect, useMemo, useState } from "react";
import { Plus, Save, Trash2, Upload, UtensilsCrossed } from "lucide-react";

import {
  createMenuItem,
  createMenuItemsBulk,
  deleteMenuItem,
  fetchMenuItems,
  updateMenuItem,
  type MenuItem,
} from "@/services/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/hooks/use-toast";

const CATEGORIES = ["وجبات", "مشروبات", "حلويات", "مقبلات", "سلطات", "إضافات"];

interface MenuTabProps {
  restaurantId?: number;
  readOnly?: boolean;
}

interface ParsedBulkLine {
  lineNumber: number;
  item?: Partial<MenuItem>;
  error?: string;
}

const parseBulkMenu = (draft: string): ParsedBulkLine[] => {
  return draft
    .split("\n")
    .map((line, index) => ({ raw: line.trim(), lineNumber: index + 1 }))
    .filter((entry) => entry.raw)
    .map(({ raw, lineNumber }) => {
      const parts = raw.split("|").map((part) => part.trim());
      if (parts.length < 3) {
        return {
          lineNumber,
          error: "لازم على الأقل اسم الصنف والفئة وسعر واحد",
        };
      }

      const [name, category, smallPrice = "0", mediumPrice = "0", largePrice = "0", ...rest] = parts;
      if (!name) {
        return { lineNumber, error: "اسم الصنف ناقص" };
      }

      return {
        lineNumber,
        item: {
          name,
          category: category || "وجبات",
          smallPrice,
          mediumPrice,
          largePrice,
          ingredients: rest.join(" | "),
          available: true,
        },
      };
    });
};

const MenuTab = ({ restaurantId, readOnly }: MenuTabProps) => {
  const { toast } = useToast();
  const [items, setItems] = useState<MenuItem[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [savingId, setSavingId] = useState<number | null>(null);
  const [bulkDraft, setBulkDraft] = useState("");
  const [bulkImporting, setBulkImporting] = useState(false);

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
      toast({ title: "تم الحفظ", description: `الصنف ${updated.name || "الجديد"} اتحفظ` });
    } catch (error) {
      console.error("Failed to save menu item:", error);
      toast({ title: "خطأ", description: "مش قادر أحفظ الصنف دلوقتي", variant: "destructive" });
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
      toast({ title: "خطأ", description: "مش قادر أمسح الصنف دلوقتي", variant: "destructive" });
    }
  };

  const parsedBulk = useMemo(() => parseBulkMenu(bulkDraft), [bulkDraft]);
  const bulkItems = parsedBulk.filter((entry) => entry.item).map((entry) => entry.item as Partial<MenuItem>);
  const bulkErrors = parsedBulk.filter((entry) => entry.error);

  const importBulkMenu = async () => {
    if (!bulkItems.length) {
      toast({ title: "مفيش بيانات", description: "حط أصناف الأول عشان نعمل import", variant: "destructive" });
      return;
    }
    if (bulkErrors.length) {
      toast({ title: "فيه أخطاء", description: "راجع السطور اللي فيها formatting غلط قبل الـ import", variant: "destructive" });
      return;
    }

    try {
      setBulkImporting(true);
      const created = await createMenuItemsBulk(bulkItems, restaurantId);
      setItems((current) => [...created, ...current]);
      setBulkDraft("");
      toast({
        title: "تم استيراد المنيو ✅",
        description: `اتضاف ${created.length} صنف مرة واحدة`,
      });
    } catch (error) {
      console.error("Failed to bulk import menu:", error);
      toast({ title: "خطأ", description: "الـ bulk import فشل، راجع البيانات وحاول تاني", variant: "destructive" });
    } finally {
      setBulkImporting(false);
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
      <CardContent className="space-y-6">
        <div className="rounded-2xl border border-border/50 bg-muted/20 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-foreground">Bulk menu import</p>
              <p className="text-xs text-muted-foreground">
                كل سطر بصيغة: `اسم الصنف | الفئة | صغير | وسط | كبير | المكونات`
              </p>
            </div>
            <div className="flex gap-2">
              <Badge variant="outline">{items.length} صنف حاليًا</Badge>
              <Badge variant="outline">{bulkItems.length} جاهزين للاستيراد</Badge>
            </div>
          </div>

          <Textarea
            value={bulkDraft}
            onChange={(event) => setBulkDraft(event.target.value)}
            className="min-h-[140px] rounded-xl bg-background"
            placeholder={"مثال:\nبرجر دبل | وجبات | 120 | 160 | 190 | لحم، جبنة شيدر\nبيبسي | مشروبات | 20 | 25 | 30 |"}
            disabled={Boolean(disabled || bulkImporting)}
          />

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs text-muted-foreground">
              {bulkErrors.length > 0
                ? `${bulkErrors.length} سطر محتاج مراجعة`
                : bulkDraft.trim()
                  ? "البيانات شكلها جاهز للاستيراد"
                  : "اكتب الأصناف هنا لو عايز تضيف المنيو بسرعة"}
            </div>
            {!readOnly && (
              <Button onClick={() => void importBulkMenu()} className="gap-2 rounded-xl" disabled={Boolean(disabled || bulkImporting)}>
                <Upload size={16} />
                {bulkImporting ? "جاري الاستيراد..." : "استيراد الأصناف"}
              </Button>
            )}
          </div>

          {bulkErrors.length > 0 && (
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/80 p-3 text-xs text-amber-700">
              {bulkErrors.slice(0, 4).map((entry) => (
                <div key={entry.lineNumber}>السطر {entry.lineNumber}: {entry.error}</div>
              ))}
            </div>
          )}
        </div>

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
