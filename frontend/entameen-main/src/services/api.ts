export interface Call {
  id: number;
  phone: string;
  lastMessage: string;
  aiResponse: string;
  time: string;
  status: "active" | "closed" | "pending";
  orderTotal: string;
}

export interface UserProfile {
  phone: string;
  totalOrders: number;
  totalSpent: string;
  lastOrder: string;
  avgOrder: string;
}

export interface Order {
  id: string;
  phone: string;
  items: string;
  amount: string;
  status: "received" | "preparing" | "ready" | "out_for_delivery" | "delivered" | "completed" | "in_progress" | "pending";
  date: string;
  upsell: boolean;
  driverPhone?: string | null;
}

export interface OrderStreamEvent {
  action: "created" | "updated";
  restaurantId: number;
  order: Order;
}

export type OrderStreamState = "connecting" | "live" | "reconnecting";

export interface FileItem {
  id: number;
  name: string;
  type: string;
  size: string;
  date: string;
  url?: string;
}

export interface ContactForm {
  restaurantName: string;
  phone: string;
  message: string;
}

export interface DashboardStats {
  totalCalls: string;
  totalOrders: string;
  totalCustomers: string;
  totalFiles: string;
}

export interface LiveKitDemoSessionRequest {
  restaurantId?: string;
  participantName?: string;
}

export interface LiveKitDemoSessionResponse {
  ok: boolean;
  livekitUrl: string;
  roomName: string;
  token: string;
  participantIdentity: string;
  participantName: string;
  restaurantId: string;
  roomMetadata: string;
  expiresInSeconds: number;
}

export interface RestaurantSettings {
  name: string;
  address: string;
  workingHours: string;
  contactPhone: string;
}

export interface AgentSettings {
  name: string;
  voiceStyle: string;
  language: string;
  personality: string;
  preCallInstructions: string;
  supplementaryInfo: string;
}

export interface SettingsResponse {
  restaurant: RestaurantSettings;
  agent: AgentSettings;
}

export interface MenuItem {
  id: number;
  name: string;
  category: string;
  ingredients: string;
  smallPrice: string;
  mediumPrice: string;
  largePrice: string;
  available?: boolean;
}

export interface Employee {
  id: number;
  name: string;
  phone: string;
  addedAt: string;
}

export interface Issue {
  id: number;
  customerPhone: string;
  description: string;
  status: "new" | "resolved";
  date: string;
  callId: string;
}

export interface AnalyticsResponse {
  summary: {
    totalRevenue: string;
    avgOrder: string;
    todayOrders: string;
    responseTime: string;
  };
  dailyOrders: Array<{ day: string; orders: number; revenue: number }>;
  monthlyRevenue: Array<{ month: string; revenue: number }>;
  categoryData: Array<{ name: string; value: number }>;
}

export interface CurrentUserProfile {
  id: number;
  name: string;
  phone: string;
  role: UserRole;
  restaurantId: number | null;
  restaurantName: string | null;
}

export interface AdminRestaurant {
  id: number;
  restaurantId: number;
  publicId: string;
  name: string;
  phone: string;
  restaurantName: string;
  plan: string;
  totalCalls: number;
  totalOrders: number;
  totalEmployees: number;
  joinedAt: string;
  status: "active" | "suspended" | "pending";
  assignedPhone: string;
  location: string;
}

export interface SalesTeamMember {
  id: number;
  name: string;
  phone: string;
}

export interface AdminSalesRequest {
  id: number;
  salesPerson: string;
  restaurantName: string;
  ownerName: string;
  ownerPhone: string;
  location: string;
  status: "pending" | "approved" | "rejected";
  date: string;
}

export interface SalesRequestRecord {
  id: number;
  restaurantName: string;
  ownerName: string;
  ownerPhone: string;
  location: string;
  status: "pending" | "approved" | "rejected";
  date: string;
}

export interface DemoSessionRecord {
  id: number;
  restaurantName: string;
  phoneNumber: string;
  status: "scheduled" | "completed" | "in_progress";
  date: string;
}

export interface AdminOverview {
  liveCalls: Array<{ restaurant: string; phone: string; duration: string; status: string }>;
  recentOrders: Array<{ restaurant: string; items: string; amount: string; status: string }>;
}

export type UserRole = "admin" | "sales" | "owner" | "employee";

function resolveDefaultApiBaseUrl() {
  if (typeof window === "undefined") {
    return "http://127.0.0.1:8000";
  }

  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  const hostname = window.location.hostname || "127.0.0.1";
  return `${protocol}//${hostname}:8000`;
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL?.trim() || resolveDefaultApiBaseUrl()).replace(/\/$/, "");
const SESSION_API_BASE_URL = (import.meta.env.VITE_SESSION_API_BASE_URL?.trim() || API_BASE_URL).replace(/\/$/, "");

export const ADMIN_PHONE = "+201094321642";
let salesPhoneCache: string[] = ["+201111111111", "+201222222222"];

function getToken() {
  return localStorage.getItem("auth_token");
}

export function getStoredToken() {
  return localStorage.getItem("auth_token");
}

export function getStoredRole(): UserRole | null {
  const role = localStorage.getItem("user_role");
  if (role === "admin" || role === "sales" || role === "owner" || role === "employee") {
    return role;
  }
  return null;
}

export function getStoredPhone() {
  return localStorage.getItem("user_phone");
}

export function storeAuthSession(token: string, phone: string, role: UserRole) {
  localStorage.setItem("auth_token", token);
  localStorage.setItem("user_phone", normalizePhone(phone));
  localStorage.setItem("user_role", role);
}

export function clearAuthSession() {
  localStorage.removeItem("auth_token");
  localStorage.removeItem("user_phone");
  localStorage.removeItem("user_role");
}

function normalizePhone(phone: string): string {
  // Keep only digits and '+'
  let stripped = phone.replace(/[^\d+]/g, "");
  if (stripped.startsWith("00")) {
    stripped = "+" + stripped.slice(2);
  }
  if (stripped.startsWith("20") && !stripped.startsWith("+20")) {
    stripped = "+" + stripped;
  }
  if (stripped.startsWith("0")) {
    stripped = "+20" + stripped.slice(1);
  }
  return stripped;
}

function buildUrl(baseUrl: string, endpoint: string, query?: Record<string, string | number | undefined | null>) {
  const url = new URL(`${baseUrl}${endpoint}`);
  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
  }
  return url.toString();
}

async function parseError(res: Response) {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string" && data.detail.trim()) {
      return data.detail;
    }
  } catch {
    // ignore parse errors
  }
  return `API Error: ${res.status}`;
}

async function apiRequest<T>(
  endpoint: string,
  options?: RequestInit,
  query?: Record<string, string | number | undefined | null>,
): Promise<T> {
  const token = getToken();
  const res = await fetch(buildUrl(API_BASE_URL, endpoint, query), {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options?.headers,
    },
    ...options,
  });

  if (!res.ok) {
    throw new Error(await parseError(res));
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json();
}

async function apiBlobRequest(endpoint: string): Promise<Blob> {
  const token = getToken();
  const res = await fetch(`${API_BASE_URL}${endpoint}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  return res.blob();
}

export function getSalesPhones(): string[] {
  return [...salesPhoneCache];
}

export function addSalesPhone(phone: string): void {
  const clean = normalizePhone(phone);
  if (!salesPhoneCache.includes(clean)) salesPhoneCache.push(clean);
}

export function removeSalesPhone(phone: string): void {
  const clean = normalizePhone(phone);
  salesPhoneCache = salesPhoneCache.filter((item) => item !== clean);
}

export function isSales(phone: string): boolean {
  return salesPhoneCache.includes(normalizePhone(phone));
}

export function isAdmin(phone: string): boolean {
  return normalizePhone(phone) === ADMIN_PHONE;
}

export async function fetchCurrentUser(): Promise<CurrentUserProfile> {
  return apiRequest<CurrentUserProfile>("/me");
}

export async function fetchCalls(restaurantId?: number): Promise<Call[]> {
  return apiRequest<Call[]>("/calls", undefined, { restaurantId });
}

export async function fetchUsers(restaurantId?: number): Promise<UserProfile[]> {
  return apiRequest<UserProfile[]>("/users", undefined, { restaurantId });
}

export async function fetchOrders(restaurantId?: number): Promise<Order[]> {
  return apiRequest<Order[]>("/orders", undefined, { restaurantId });
}

export function subscribeToOrderEvents(options: {
  restaurantId?: number;
  onEvent: (event: OrderStreamEvent) => void;
  onStateChange?: (state: OrderStreamState) => void;
  onError?: (error: Event) => void;
}): () => void {
  if (typeof window === "undefined") {
    return () => {};
  }

  const token = getToken();
  if (!token) {
    options.onStateChange?.("reconnecting");
    return () => {};
  }

  const source = new EventSource(
    buildUrl(API_BASE_URL, "/orders/stream", {
      restaurantId: options.restaurantId,
      token,
    }),
  );

  options.onStateChange?.("connecting");

  source.onopen = () => {
    options.onStateChange?.("live");
  };

  source.addEventListener("order_update", (event) => {
    options.onStateChange?.("live");
    if (!(event instanceof MessageEvent)) {
      return;
    }

    try {
      options.onEvent(JSON.parse(event.data) as OrderStreamEvent);
    } catch (error) {
      console.error("Failed to parse order stream event:", error);
    }
  });

  source.onerror = (event) => {
    options.onStateChange?.("reconnecting");
    options.onError?.(event);
  };

  return () => {
    source.close();
  };
}

export async function fetchFiles(restaurantId?: number): Promise<FileItem[]> {
  return apiRequest<FileItem[]>("/files", undefined, { restaurantId });
}

export async function fetchDashboardStats(restaurantId?: number): Promise<DashboardStats> {
  return apiRequest<DashboardStats>("/stats", undefined, { restaurantId });
}

export async function submitContactForm(data: ContactForm): Promise<boolean> {
  return apiRequest<boolean>("/contact", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function createLiveKitDemoSession(
  data: LiveKitDemoSessionRequest = {},
): Promise<LiveKitDemoSessionResponse> {
  const res = await fetch(`${SESSION_API_BASE_URL}/demo/livekit-session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

  if (!res.ok) {
    throw new Error(await parseError(res));
  }

  return res.json();
}

export async function loginWithPhone(phone: string): Promise<{ success: boolean }> {
  return apiRequest<{ success: boolean }>("/auth/send-otp", {
    method: "POST",
    body: JSON.stringify({ phone }),
  });
}

export async function verifyOtp(
  phone: string,
  otp: string,
): Promise<{ success: boolean; token?: string; role?: UserRole }> {
  return apiRequest<{ success: boolean; token?: string; role?: UserRole }>("/auth/verify-otp", {
    method: "POST",
    body: JSON.stringify({ phone, otp }),
  });
}

export async function updateOrderStatus(
  orderId: string,
  statusValue: string,
  driverPhone?: string,
): Promise<Order> {
  return apiRequest<Order>(`/orders/${orderId}`, {
    method: "PATCH",
    body: JSON.stringify({ status: statusValue, driverPhone }),
  });
}

export async function fetchSettings(restaurantId?: number): Promise<SettingsResponse> {
  return apiRequest<SettingsResponse>("/settings", undefined, { restaurantId });
}

export async function saveSettings(payload: SettingsResponse, restaurantId?: number): Promise<SettingsResponse> {
  return apiRequest<SettingsResponse>(
    "/settings",
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
    { restaurantId },
  );
}

export async function fetchMenuItems(restaurantId?: number): Promise<MenuItem[]> {
  return apiRequest<MenuItem[]>("/menu-items", undefined, { restaurantId });
}

export async function createMenuItem(payload: Partial<MenuItem>, restaurantId?: number): Promise<MenuItem> {
  return apiRequest<MenuItem>(
    "/menu-items",
    {
      method: "POST",
      body: JSON.stringify({
        name: payload.name ?? "",
        category: payload.category ?? "وجبات",
        ingredients: payload.ingredients ?? "",
        smallPrice: payload.smallPrice ?? "0",
        mediumPrice: payload.mediumPrice ?? "0",
        largePrice: payload.largePrice ?? "0",
        available: payload.available ?? true,
      }),
    },
    { restaurantId },
  );
}

export async function updateMenuItem(itemId: number, payload: Partial<MenuItem>, restaurantId?: number): Promise<MenuItem> {
  return apiRequest<MenuItem>(
    `/menu-items/${itemId}`,
    {
      method: "PUT",
      body: JSON.stringify({
        name: payload.name ?? "",
        category: payload.category ?? "وجبات",
        ingredients: payload.ingredients ?? "",
        smallPrice: payload.smallPrice ?? "0",
        mediumPrice: payload.mediumPrice ?? "0",
        largePrice: payload.largePrice ?? "0",
        available: payload.available ?? true,
      }),
    },
    { restaurantId },
  );
}

export async function deleteMenuItem(itemId: number, restaurantId?: number): Promise<void> {
  await apiRequest<void>(`/menu-items/${itemId}`, { method: "DELETE" }, { restaurantId });
}

export async function fetchEmployees(restaurantId?: number): Promise<Employee[]> {
  return apiRequest<Employee[]>("/employees", undefined, { restaurantId });
}

export async function createEmployee(name: string, phone: string, restaurantId?: number): Promise<Employee> {
  return apiRequest<Employee>(
    "/employees",
    {
      method: "POST",
      body: JSON.stringify({ name, phone }),
    },
    { restaurantId },
  );
}

export async function deleteEmployee(employeeId: number, restaurantId?: number): Promise<void> {
  await apiRequest<void>(`/employees/${employeeId}`, { method: "DELETE" }, { restaurantId });
}

export async function fetchIssues(restaurantId?: number): Promise<Issue[]> {
  return apiRequest<Issue[]>("/issues", undefined, { restaurantId });
}

export async function updateIssueStatus(issueId: number, statusValue: "new" | "resolved", restaurantId?: number): Promise<Issue> {
  return apiRequest<Issue>(
    `/issues/${issueId}`,
    {
      method: "PATCH",
      body: JSON.stringify({ status: statusValue }),
    },
    { restaurantId },
  );
}

export async function fetchAnalytics(restaurantId?: number): Promise<AnalyticsResponse> {
  return apiRequest<AnalyticsResponse>("/analytics", undefined, { restaurantId });
}

export async function fetchAdminRestaurants(): Promise<AdminRestaurant[]> {
  return apiRequest<AdminRestaurant[]>("/admin/restaurants");
}

export async function createAdminRestaurant(payload: {
  name: string;
  ownerName: string;
  ownerPhone: string;
  location: string;
  plan: string;
  assignedPhone: string;
}): Promise<AdminRestaurant> {
  return apiRequest<AdminRestaurant>("/admin/restaurants", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminSalesTeam(): Promise<SalesTeamMember[]> {
  const members = await apiRequest<SalesTeamMember[]>("/admin/sales-team");
  salesPhoneCache = members.map((member) => member.phone);
  return members;
}

export async function createAdminSalesMember(name: string, phone: string): Promise<SalesTeamMember> {
  const member = await apiRequest<SalesTeamMember>("/admin/sales-team", {
    method: "POST",
    body: JSON.stringify({ name, phone }),
  });
  addSalesPhone(member.phone);
  return member;
}

export async function deleteAdminSalesMember(memberId: number, phone: string): Promise<void> {
  await apiRequest<void>(`/admin/sales-team/${memberId}`, { method: "DELETE" });
  removeSalesPhone(phone);
}

export async function fetchAdminSalesRequests(): Promise<AdminSalesRequest[]> {
  return apiRequest<AdminSalesRequest[]>("/admin/sales-requests");
}

export async function updateAdminSalesRequestStatus(
  requestId: number,
  statusValue: "approved" | "rejected",
): Promise<AdminSalesRequest> {
  return apiRequest<AdminSalesRequest>(`/admin/sales-requests/${requestId}`, {
    method: "PATCH",
    body: JSON.stringify({ status: statusValue }),
  });
}

export async function fetchAdminOverview(): Promise<AdminOverview> {
  return apiRequest<AdminOverview>("/admin/overview");
}

export async function fetchSalesRequests(): Promise<SalesRequestRecord[]> {
  return apiRequest<SalesRequestRecord[]>("/sales/requests");
}

export async function submitSalesRequest(payload: {
  restaurantName: string;
  ownerName: string;
  ownerPhone: string;
  location: string;
}): Promise<SalesRequestRecord> {
  return apiRequest<SalesRequestRecord>("/sales/requests", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function fetchDemoSessions(): Promise<DemoSessionRecord[]> {
  return apiRequest<DemoSessionRecord[]>("/sales/demo-sessions");
}

export async function createDemoSession(payload: {
  restaurantName: string;
  phoneNumber: string;
}): Promise<DemoSessionRecord> {
  return apiRequest<DemoSessionRecord>("/sales/demo-sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function uploadFile(file: File): Promise<FileItem> {
  const token = getToken();
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE_URL}/files/upload`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: formData,
  });
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  return res.json();
}

export async function downloadFile(fileId: number): Promise<Blob> {
  return apiBlobRequest(`/files/${fileId}/download`);
}

export async function previewFile(fileId: number): Promise<string> {
  const data = await apiRequest<{ url: string }>(`/files/${fileId}/preview`);
  return data.url;
}
