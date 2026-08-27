export type SessionMetadata = {
  csrf_token: string;
  subject: string;
  roles: string[];
  org_ids: string[];
  expires_at: string;
};

export type ActivationState = "active" | "waiting" | "degraded" | "inactive" | "unknown";

export type ActivationStatus = {
  state: ActivationState;
  connectedDeployments: number;
  activeSessions: number;
  lastTelemetryAt: string | null;
  message: string | null;
  missingSignals: string[];
};

export type FleetDashboard = {
  window: { from: string; to: string; granularity: string };
  totals: {
    active_sessions: number;
    action_count: number;
    error_rate: number;
    total_cost_usd: number;
    total_input_tokens: number;
    total_output_tokens: number;
    active_session_inactivity_minutes: number;
    stale_active_sessions_excluded: number;
  };
  timeseries: Array<Record<string, unknown>>;
  top_agents: Array<{ agent_id: string; action_count: number; total_cost_usd: number }>;
  top_resources: Array<{ resource: string; action_count: number }>;
};

export type Anomaly = {
  id: string;
  deployment_id: string | null;
  session_id: string | null;
  trace_id: string | null;
  action_id: string | null;
  anomaly_type: string;
  severity: string;
  status: string;
  detector_name: string;
  baseline_value: number | null;
  observed_value: number | null;
  deviation_ratio: number | null;
  score: number | null;
  title: string;
  description: string | null;
  detected_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  updated_by: string | null;
  note: string | null;
  metadata: Record<string, unknown>;
};

export type AnomalyGroup = {
  fingerprint: string;
  anomaly_type: string;
  title: string;
  deployment_id: string | null;
  representative_anomaly_id: string;
  representative_severity: string;
  latest_detector_name: string | null;
  first_detected_at: string;
  last_detected_at: string;
  anomaly_count: number;
  total_occurrences: number;
  open_count: number;
  acknowledged_count: number;
  resolved_count: number;
};

export type TraceListItem = {
  id: string;
  correlation_id: string;
  trace_type: string;
  status: string;
  started_at: string;
  duration_ms: number | null;
  total_input_tokens: number;
  total_output_tokens: number;
  estimated_cost_usd: number;
  span_count: number;
  tags: string[];
};

export type ReasoningStep = {
  id: string;
  step_number: number;
  step_type: string;
  description: string;
  dimension: string | null;
  explanation: string | null;
  confidence: number | null;
};

export type TraceSpan = {
  id: string;
  span_type: string;
  name: string;
  provider: string | null;
  model: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  status: string;
  error_message: string | null;
  reasoning_steps: ReasoningStep[];
};

export type TraceDetail = TraceListItem & {
  idea_id: number | null;
  ranking_id: number | null;
  completed_at: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  spans: TraceSpan[];
};

export type Page<T> = {
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
} & T;
