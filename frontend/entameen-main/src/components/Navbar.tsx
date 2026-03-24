import { useState } from "react";
import { Link } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { motion, AnimatePresence } from "framer-motion";
import logo from "@/assets/logo.png";

const navLinks = [
  { label: "الرئيسية", path: "/" },
  { label: "خدماتنا", path: "/#services" },
  { label: "الأسعار", path: "/pricing" },
  { label: "مين إحنا", path: "/#about" },
  { label: "كلمنا", path: "/#contact" },
];

const Navbar = () => {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <nav className="fixed inset-x-0 top-0 z-50 glass border-b border-border/50">
      <div className="container mx-auto flex items-center justify-between h-16 px-4">
        <Link to="/" className="flex items-center gap-2.5">
          <img
            src={logo}
            alt="ألو إيچي"
            className="w-8 h-8 object-contain"
          />
          <span className="text-xl font-heading font-bold text-foreground">ألو إيچي</span>
        </Link>

        <div className="hidden md:flex items-center gap-7">
          {navLinks.map((link) => (
            link.path.startsWith("/#") ? (
              <a key={link.path} href={link.path} className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
                {link.label}
              </a>
            ) : (
              <Link key={link.path} to={link.path} className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
                {link.label}
              </Link>
            )
          ))}
        </div>

        <div className="hidden md:flex items-center gap-3">
          <Link to="/login">
            <Button size="sm" className="rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-brand px-5">
              دخول
            </Button>
          </Link>
        </div>

        <button className="md:hidden p-2 text-foreground" onClick={() => setMobileOpen(!mobileOpen)}>
          {mobileOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </div>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="md:hidden glass border-t border-border/50"
          >
            <div className="flex flex-col p-4 gap-1">
              {navLinks.map((link) => (
                link.path.startsWith("/#") ? (
                  <a key={link.path} href={link.path} className="py-2.5 px-3 rounded-lg text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors" onClick={() => setMobileOpen(false)}>
                    {link.label}
                  </a>
                ) : (
                  <Link key={link.path} to={link.path} className="py-2.5 px-3 rounded-lg text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors" onClick={() => setMobileOpen(false)}>
                    {link.label}
                  </Link>
                )
              ))}
              <Link to="/login" onClick={() => setMobileOpen(false)} className="mt-2">
                <Button className="w-full rounded-lg bg-primary text-primary-foreground">
                  دخول
                </Button>
              </Link>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </nav>
  );
};

export default Navbar;
