import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Loader2, LogOut, User } from "lucide-react";

import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import logo from "@/assets/logo.png";
import OwnerDashboardView from "@/components/admin/OwnerDashboardView";
import { clearAuthSession, fetchCurrentUser, type CurrentUserProfile } from "@/services/api";

const Dashboard = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [currentUser, setCurrentUser] = useState<CurrentUserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let ignore = false;

    const loadUser = async () => {
      try {
        setLoading(true);
        const profile = await fetchCurrentUser();
        if (!ignore) {
          setCurrentUser(profile);
        }
      } catch (error) {
        console.error("Failed to load current user:", error);
        if (!ignore) {
          clearAuthSession();
          toast({
            title: "الجلسة انتهت",
            description: "سجل دخولك تاني علشان نكمّل",
            variant: "destructive",
          });
          navigate("/login", { replace: true });
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    };

    void loadUser();
    return () => {
      ignore = true;
    };
  }, [navigate, toast]);

  const handleLogout = () => {
    clearAuthSession();
    navigate("/login", { replace: true });
  };

  if (loading || !currentUser) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <Loader2 size={18} className="animate-spin text-primary" />
          بنجهز لوحة المطعم...
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="glass sticky top-0 z-40 border-b border-border/50">
        <div className="container mx-auto flex h-14 items-center justify-between px-4">
          <Link to="/" className="flex items-center gap-2">
            <img src={logo} alt="ألو إيچي" className="h-7 w-7 object-contain" />
            <span className="text-lg font-heading font-bold text-foreground">ألو إيچي</span>
          </Link>
          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-2 text-sm text-muted-foreground sm:flex">
              <User size={15} />
              <span>{currentUser.name}</span>
              <span dir="ltr">{currentUser.phone}</span>
            </div>
            <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={handleLogout}>
              <LogOut size={16} />
            </Button>
          </div>
        </div>
      </div>

      <div className="container mx-auto px-4 py-6">
        <OwnerDashboardView
          restaurantName={currentUser.restaurantName ?? "مطعمي"}
          restaurantId={currentUser.restaurantId ?? undefined}
        />
      </div>
    </div>
  );
};

export default Dashboard;
