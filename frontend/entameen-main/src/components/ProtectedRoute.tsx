import { Navigate, Outlet } from "react-router-dom";

import { getStoredRole, getStoredToken, type UserRole } from "@/services/api";

interface ProtectedRouteProps {
  allowedRoles: UserRole[];
}

const defaultPathByRole: Record<UserRole, string> = {
  admin: "/admin",
  sales: "/sales",
  owner: "/dashboard",
  employee: "/dashboard",
};

const ProtectedRoute = ({ allowedRoles }: ProtectedRouteProps) => {
  const token = getStoredToken();
  const role = getStoredRole();

  if (!token || !role) {
    return <Navigate to="/login" replace />;
  }

  if (!allowedRoles.includes(role)) {
    return <Navigate to={defaultPathByRole[role]} replace />;
  }

  return <Outlet />;
};

export default ProtectedRoute;
