export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

let csrfToken: string | null = null;

export function apiUrl(path: string) {
  const base = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
  if (!base || path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export function setCsrf(token: string | null) {
  csrfToken = token;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const method = (options.method || "GET").toUpperCase();
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken && method !== "GET" && method !== "HEAD") {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(apiUrl(path), { ...options, headers, credentials: "include" });
  if (response.status === 204) {
    return undefined as T;
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new ApiError(
        response.status || 502,
        "BAD_RESPONSE",
        "The attendance service did not finish answering. Check History before marking this day again.",
      );
    }
  }
  if (!response.ok) {
    throw new ApiError(response.status, data?.error?.code || "ERROR", data?.error?.message || "The request failed.");
  }
  if (data && typeof data === "object" && "csrf_token" in data && data.csrf_token) {
    csrfToken = String(data.csrf_token);
  }
  return data as T;
}

export type TimeView = { utc: string; pkt: string; pkt_display: string };

export type Account = {
  id: string;
  email: string;
  display_name: string;
  role: "ADMIN" | "MANAGER";
  is_active: boolean;
  must_change_password: boolean;
  mfa_enabled: boolean;
  passkey_count?: number;
  timenex_mapping?: {
    timenex_employee_id: string;
    verification_status: string;
    account_status: string;
    authenticates_by_itself: boolean;
  } | null;
  remote_check_in_ready?: boolean;
  remote_check_in?: string;
  timenex_confirmed?: boolean;
  employee: {
    id: string;
    employee_number: string;
    full_name: string;
    designation: string | null;
    department: string | null;
    device_pin: string | null;
    remote_attendance_enabled: boolean;
    is_active: boolean;
    is_test_record: boolean;
  } | null;
};

export type Me = {
  csrf_token: string;
  environment: string;
  user: Account;
  policy: { remote_attendance_approved: boolean; inference_enabled: boolean };
  server_time: TimeView;
};

export type AttendanceEvent = {
  id: string;
  employee_number: string | null;
  employee_name: string | null;
  department: string | null;
  is_test_record: boolean;
  event_type: "CHECK_IN" | "CHECK_OUT";
  source: string;
  source_label: string;
  status: string;
  server_received_at: TimeView;
  effective_at: TimeView;
  client_reported_at: TimeView | null;
  external_event_id: string | null;
  reconciliation_status: string | null;
  authentication_method?: string | null;
  authentication_label?: string | null;
  client_platform?: string | null;
  user_verification?: string | null;
  location_checked?: boolean;
  timenex_employee_id?: string | null;
  timenex_mapping_label?: string;
  timenex_sync_status?: string;
  timenex_confirmed?: boolean;
  timenex_label?: string;
  timenex_error?: string | null;
};

export type TimenexReceipt = {
  confirmed: boolean;
  submitted: boolean;
  status: string;
  timenex_employee_id?: string | null;
  reference?: string | null;
  message: string;
};

export function pktNowLabel() {
  return new Intl.DateTimeFormat("en-PK", {
    timeZone: "Asia/Karachi",
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date());
}

export function pktDateInput(daysAgo = 0) {
  const date = new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Karachi",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function greeting() {
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Karachi", hour: "numeric", hourCycle: "h23" }).format(
      new Date(),
    ),
  );
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}
