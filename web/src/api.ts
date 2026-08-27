import type {
  ActivationState,
  ActivationStatus,
  Anomaly,
  AnomalyGroup,
  FleetDashboard,
  Page,
  SessionMetadata,
  TraceDetail,
  TraceListItem,
} from "./types";

type RequestContext = {
  orgId?: string;
  csrfToken?: string;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const asRecord = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};

const asNumber = (value: unknown): number =>
  typeof value === "number" && Number.isFinite(value) ? value : 0;

const asStringArray = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

function normalizeActivation(payload: unknown): ActivationStatus {
  const raw = asRecord(payload);
  const rawState = String(raw.state ?? raw.status ?? "unknown").toLowerCase();
  const stateAliases: Record<string, ActivationState> = {
    activated: "active",
    active: "active",
    healthy: "active",
    pending: "waiting",
    waiting: "waiting",
    onboarding: "waiting",
    awaiting_telemetry: "waiting",
    degraded: "degraded",
    stale: "degraded",
    inactive: "inactive",
    not_started: "inactive",
    setup_required: "inactive",
  };
  const state = stateAliases[rawState] ?? (raw.is_active === true ? "active" : "unknown");

  return {
    state,
    connectedDeployments: asNumber(raw.connected_deployments ?? raw.deployment_count),
    activeSessions: asNumber(raw.active_sessions ?? raw.session_count),
    lastTelemetryAt:
      typeof (raw.last_telemetry_at ?? raw.last_event_at) === "string"
        ? String(raw.last_telemetry_at ?? raw.last_event_at)
        : null,
    message: typeof raw.message === "string" ? raw.message : null,
    missingSignals: asStringArray(raw.missing_signals ?? raw.missing_requirements),
  };
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  context: RequestContext = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (context.orgId) headers.set("X-Org-Id", context.orgId);
  if (context.csrfToken) headers.set("X-CSRF-Token", context.csrfToken);

  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = asRecord(await response.json());
      if (typeof body.detail === "string") message = body.detail;
      if (typeof body.message === "string") message = body.message;
    } catch {
      // Keep the status-based message when a proxy returns a non-JSON error page.
    }
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function windowQuery(hours = 24): URLSearchParams {
  const to = new Date();
  const from = new Date(to.getTime() - hours * 60 * 60 * 1000);
  return new URLSearchParams({ from: from.toISOString(), to: to.toISOString() });
}

export const api = {
  login(apiKey: string): Promise<SessionMetadata> {
    return request("/api/v1/auth/browser/sessions", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
    });
  },

  session(): Promise<SessionMetadata> {
    return request("/api/v1/auth/browser/session");
  },

  logout(csrfToken: string): Promise<void> {
    return request(
      "/api/v1/auth/browser/session",
      { method: "DELETE" },
      { csrfToken },
    );
  },

  async activation(orgId: string): Promise<ActivationStatus> {
    const query = new URLSearchParams({ org_id: orgId });
    const payload = await request<unknown>(
      `/api/v1/observability/activation/status?${query}`,
      {},
      { orgId },
    );
    return normalizeActivation(payload);
  },

  fleet(orgId: string): Promise<FleetDashboard> {
    const query = windowQuery();
    query.set("org_id", orgId);
    query.set("granularity", "1h");
    return request(`/api/v1/observability/dashboard/fleet?${query}`, {}, { orgId });
  },

  anomalyGroups(orgId: string): Promise<Page<{ groups: AnomalyGroup[] }>> {
    const query = windowQuery(24 * 7);
    query.set("org_id", orgId);
    query.set("page_size", "8");
    return request(`/api/v1/observability/anomalies/groups?${query}`, {}, { orgId });
  },

  anomalies(
    orgId: string,
    filters: { status?: string; severity?: string; page?: number } = {},
  ): Promise<Page<{ anomalies: Anomaly[] }>> {
    const query = windowQuery(24 * 30);
    query.set("org_id", orgId);
    query.set("page", String(filters.page ?? 1));
    query.set("page_size", "30");
    if (filters.status) query.set("status", filters.status);
    if (filters.severity) query.set("severity", filters.severity);
    return request(`/api/v1/observability/anomalies?${query}`, {}, { orgId });
  },

  anomaly(orgId: string, anomalyId: string): Promise<Anomaly> {
    const query = new URLSearchParams({ org_id: orgId });
    return request(`/api/v1/observability/anomalies/${anomalyId}?${query}`, {}, { orgId });
  },

  updateAnomaly(
    orgId: string,
    anomalyId: string,
    csrfToken: string,
    payload: { status: string; note?: string },
  ): Promise<Anomaly> {
    return request(
      `/api/v1/observability/anomalies/${anomalyId}`,
      { method: "PATCH", body: JSON.stringify(payload) },
      { orgId, csrfToken },
    );
  },

  traces(orgId: string): Promise<Page<{ traces: TraceListItem[] }>> {
    const query = new URLSearchParams({ org_id: orgId, page: "1", page_size: "8" });
    return request(`/api/v1/traces?${query}`, {}, { orgId });
  },

  trace(orgId: string, traceId: string): Promise<TraceDetail> {
    const query = new URLSearchParams({ include_prompts: "false" });
    return request(`/api/v1/traces/${traceId}?${query}`, {}, { orgId });
  },
};
