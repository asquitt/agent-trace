import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  Clock3,
  Fingerprint,
  Gauge,
  KeyRound,
  ListFilter,
  LogOut,
  RefreshCw,
  RotateCcw,
  Search,
  ServerCog,
  ShieldCheck,
  TriangleAlert,
  UserRoundCheck,
  XCircle,
} from "lucide-react";
import {
  FormEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";

import { api, ApiError } from "./api";
import type {
  ActivationStatus,
  Anomaly,
  AnomalyGroup,
  FleetDashboard,
  SessionMetadata,
  TraceDetail,
  TraceListItem,
} from "./types";

type ResourceState<T> =
  | { status: "loading"; data: null; error: null; updatedAt: null }
  | { status: "error"; data: null; error: Error; updatedAt: null }
  | { status: "success"; data: T; error: null; updatedAt: Date };

type OverviewData = {
  activation: ActivationStatus | null;
  fleet: FleetDashboard | null;
  groups: AnomalyGroup[];
  traces: TraceListItem[];
  failures: string[];
};

type ConsoleContext = {
  session: SessionMetadata;
  orgId: string;
  setOrgId: (orgId: string) => void;
  signOut: () => Promise<void>;
};

const SELECTED_ORG_STORAGE_KEY = "ai-trace.selected-org";

function storedOrganization(): string | null {
  try {
    return window.sessionStorage.getItem(SELECTED_ORG_STORAGE_KEY);
  } catch {
    return null;
  }
}

function persistOrganization(orgId: string | null) {
  try {
    if (orgId) window.sessionStorage.setItem(SELECTED_ORG_STORAGE_KEY, orgId);
    else window.sessionStorage.removeItem(SELECTED_ORG_STORAGE_KEY);
  } catch {
    // Continue without tab persistence when browser storage is unavailable.
  }
}

const emptyResource = <T,>(): ResourceState<T> => ({
  status: "loading",
  data: null,
  error: null,
  updatedAt: null,
});

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not recorded";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatRelative(value: string | null | undefined): string {
  if (!value) return "No signal";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return "No signal";
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60_000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1_000) return `${Math.round(value)} ms`;
  return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)} s`;
}

function formatUsd(value: number | null | undefined): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: value && value < 1 ? 4 : 2,
  }).format(value ?? 0);
}

function readable(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function hasOperatorRole(roles: string[]): boolean {
  return roles.some((role) => role === "operator" || role === "admin" || role === "admin:*");
}

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Your operator session expired. Sign in again.";
    if (error.status === 403) return "This session does not have access to that evidence.";
  }
  return error instanceof Error ? error.message : "The evidence service did not respond.";
}

function useResource<T>(key: string, loader: () => Promise<T>) {
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const [state, setState] = useState<ResourceState<T>>(emptyResource<T>);
  const requestRef = useRef(0);

  const reload = useCallback(async () => {
    const requestId = ++requestRef.current;
    setState((current) => ({
      status: "loading",
      data: current.status === "success" ? current.data : null,
      error: null,
      updatedAt: current.status === "success" ? current.updatedAt : null,
    }) as ResourceState<T>);
    try {
      const data = await loaderRef.current();
      if (requestId === requestRef.current) {
        setState({ status: "success", data, error: null, updatedAt: new Date() });
      }
    } catch (error) {
      if (requestId === requestRef.current) {
        setState({
          status: "error",
          data: null,
          error: error instanceof Error ? error : new Error(String(error)),
          updatedAt: null,
        });
      }
    }
  }, []);

  useEffect(() => {
    void reload();
    return () => {
      requestRef.current += 1;
    };
  }, [key, reload]);

  return { state, reload };
}

function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${title} · AI Trace`;
  }, [title]);
}

function StateBadge({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const tone = ["critical", "failed", "error", "open", "degraded"].includes(normalized)
    ? "fault"
    : ["high", "warning", "acknowledged", "waiting", "running"].includes(normalized)
      ? "warning"
      : ["active", "healthy", "success", "resolved", "completed"].includes(normalized)
        ? "ok"
        : "neutral";
  return <span className={`state-badge state-badge--${tone}`}>{readable(value)}</span>;
}

function LoadingBlock({ label = "Loading operational evidence" }: { label?: string }) {
  return (
    <div className="state-panel state-panel--loading" role="status" aria-live="polite">
      <span className="loader-mark" aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <p>Reading the latest persisted signals.</p>
      </div>
    </div>
  );
}

function ErrorBlock({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className="state-panel state-panel--error" role="alert">
      <XCircle aria-hidden="true" />
      <div>
        <strong>Evidence unavailable</strong>
        <p>{getErrorMessage(error)}</p>
      </div>
      <button className="button button--quiet" type="button" onClick={retry}>
        <RotateCcw size={16} aria-hidden="true" /> Retry
      </button>
    </div>
  );
}

function EmptyBlock({ title, body }: { title: string; body: string }) {
  return (
    <div className="state-panel state-panel--empty">
      <CircleDot aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{body}</p>
      </div>
    </div>
  );
}

function LoginPage({ onLogin }: { onLogin: (session: SessionMetadata) => void }) {
  usePageTitle("Sign in");
  const [apiKey, setApiKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submittedKey = apiKey.trim();
    if (!submittedKey) {
      setError("Enter the operator API key supplied by your administrator.");
      return;
    }
    setApiKey("");
    setSubmitting(true);
    setError(null);
    try {
      onLogin(await api.login(submittedKey));
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-thesis" aria-labelledby="login-title">
        <div className="brand-lockup brand-lockup--inverse">
          <span className="brand-glyph" aria-hidden="true">A/T</span>
          <span>AI Trace</span>
        </div>
        <div className="login-thesis__content">
          <p className="eyebrow eyebrow--inverse">Operator console</p>
          <h1 id="login-title">Follow the signal.<br />Prove the cause.</h1>
          <p>
            Verify live telemetry, isolate fleet risk, and close anomalies with the exact trace
            evidence attached.
          </p>
        </div>
        <div className="login-pulse" aria-hidden="true">
          <span className="login-pulse__node login-pulse__node--live" />
          <span className="login-pulse__line" />
          <span className="login-pulse__node" />
          <span className="login-pulse__line" />
          <span className="login-pulse__node" />
          <span className="login-pulse__labels"><b>Fleet</b><b>Anomaly</b><b>Trace</b></span>
        </div>
      </section>

      <section className="login-form-wrap" aria-label="Sign in form">
        <form className="login-form" onSubmit={submit}>
          <div className="login-form__heading">
            <span className="step-label">Secure session / 01</span>
            <h2>Open the evidence workspace</h2>
            <p>The key is exchanged once for a protected browser session and is never stored.</p>
          </div>

          <label className="field-label" htmlFor="api-key">Operator API key</label>
          <div className="input-with-icon">
            <KeyRound size={18} aria-hidden="true" />
            <input
              id="api-key"
              name="operator-access-token"
              type="password"
              autoComplete="off"
              spellCheck="false"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? errorId : undefined}
              placeholder="Paste key"
              autoFocus
            />
          </div>
          {error && <p className="field-error" id={errorId} role="alert">{error}</p>}

          <button className="button button--primary button--wide" type="submit" disabled={submitting}>
            {submitting ? "Establishing session…" : "Continue to organizations"}
            {!submitting && <ArrowRight size={18} aria-hidden="true" />}
          </button>
          <p className="security-note"><ShieldCheck size={16} aria-hidden="true" /> Same-origin, HttpOnly session protection</p>
        </form>
      </section>
    </main>
  );
}

function OrgSelectionPage({
  session,
  choose,
  signOut,
}: {
  session: SessionMetadata;
  choose: (orgId: string) => void;
  signOut: () => Promise<void>;
}) {
  usePageTitle("Choose organization");
  const navigate = useNavigate();

  function chooseOrganization(orgId: string) {
    choose(orgId);
    navigate("/overview");
  }

  return (
    <main className="org-shell">
      <div className="org-shell__top">
        <div className="brand-lockup"><span className="brand-glyph">A/T</span><span>AI Trace</span></div>
        <button className="button button--quiet" type="button" onClick={() => void signOut()}>
          <LogOut size={16} aria-hidden="true" /> Sign out
        </button>
      </div>
      <section className="org-chooser" aria-labelledby="org-title">
        <div className="org-chooser__intro">
          <p className="eyebrow">Access boundary</p>
          <h1 id="org-title">Which fleet are you investigating?</h1>
          <p>
            Each workspace keeps evidence and controls inside one organization. You can switch
            later without ending this session.
          </p>
        </div>
        <div className="identity-strip">
          <UserRoundCheck size={18} aria-hidden="true" />
          <span>Signed in as <strong>{session.subject}</strong></span>
          <span className="identity-strip__roles">{session.roles.join(" · ")}</span>
        </div>
        {session.org_ids.length === 0 ? (
          <EmptyBlock
            title="No organization access"
            body="Ask an administrator to add at least one organization to this operator key."
          />
        ) : (
          <ul className="org-list" aria-label="Available organizations">
            {session.org_ids.map((orgId, index) => (
              <li key={orgId}>
                <button className="org-row" type="button" onClick={() => chooseOrganization(orgId)}>
                  <span className="org-row__index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="org-row__name">{orgId}</span>
                  <span className="org-row__access">Authorized workspace</span>
                  <ArrowRight size={19} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function PulseRail() {
  const location = useLocation();
  const current = location.pathname.startsWith("/traces")
    ? "trace"
    : location.pathname.startsWith("/anomalies")
      ? "anomaly"
      : "fleet";
  const steps = [
    { id: "fleet", label: "Fleet", to: "/overview", icon: Gauge },
    { id: "anomaly", label: "Anomalies", to: "/anomalies", icon: AlertTriangle },
    { id: "trace", label: "Trace", to: location.pathname.startsWith("/traces") ? location.pathname : "", icon: Fingerprint },
  ];

  return (
    <aside className="command-rail" aria-label="Evidence journey">
      <Link className="rail-brand" to="/overview" aria-label="AI Trace overview">A/T</Link>
      <nav className="pulse-nav" aria-label="Primary navigation">
        {steps.map(({ id, label, to, icon: Icon }, index) => {
          const disabled = !to;
          const content = (
            <>
              <span className={`pulse-node ${id === current ? "pulse-node--current" : ""}`}>
                <Icon size={17} aria-hidden="true" />
              </span>
              <span className="pulse-nav__label">{label}</span>
              {index < steps.length - 1 && <span className="pulse-line" aria-hidden="true" />}
            </>
          );
          return disabled ? (
            <span className="pulse-nav__item pulse-nav__item--disabled" key={id} aria-disabled="true">{content}</span>
          ) : (
            <Link className="pulse-nav__item" key={id} to={to} aria-current={id === current ? "page" : undefined}>{content}</Link>
          );
        })}
      </nav>
      <span className="rail-environment">OPS</span>
    </aside>
  );
}

function ConsoleShell({ context }: { context: ConsoleContext }) {
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    mainRef.current?.focus();
  }, [location.pathname]);

  return (
    <div className="console-shell">
      <a className="skip-link" href="#main-content">Skip to evidence</a>
      <PulseRail />
      <div className="console-frame">
        <header className="topbar">
          <div className="topbar__context">
            <span className="live-pin"><span /> Session protected</span>
            <label htmlFor="org-switcher">Organization</label>
            <div className="select-wrap select-wrap--topbar">
              <select
                id="org-switcher"
                value={context.orgId}
                onChange={(event) => context.setOrgId(event.target.value)}
              >
                {context.session.org_ids.map((orgId) => <option key={orgId}>{orgId}</option>)}
              </select>
              <ChevronDown size={15} aria-hidden="true" />
            </div>
          </div>
          <div className="topbar__actions">
            <span className="role-mark">{context.session.roles[0] ?? "viewer"}</span>
            <button className="icon-button" type="button" onClick={() => void context.signOut()} aria-label="Sign out">
              <LogOut size={18} aria-hidden="true" />
            </button>
          </div>
        </header>
        <main id="main-content" className="workspace" ref={mainRef} tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function PageHeading({
  eyebrow,
  title,
  description,
  updatedAt,
  refresh,
}: {
  eyebrow: string;
  title: string;
  description: string;
  updatedAt?: Date | null;
  refresh?: () => void;
}) {
  return (
    <header className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {refresh && (
        <div className="refresh-control">
          <span>{updatedAt ? `Updated ${formatDate(updatedAt.toISOString())}` : "Update pending"}</span>
          <button className="button button--quiet" type="button" onClick={refresh}>
            <RefreshCw size={16} aria-hidden="true" /> Refresh
          </button>
        </div>
      )}
    </header>
  );
}

function OverviewPage({ context }: { context: ConsoleContext }) {
  usePageTitle("Fleet overview");
  const { state, reload } = useResource(`overview:${context.orgId}`, async () => {
    const results = await Promise.allSettled([
      api.activation(context.orgId),
      api.fleet(context.orgId),
      api.anomalyGroups(context.orgId),
      api.traces(context.orgId),
    ]);
    const failures: string[] = [];
    const value = <T,>(result: PromiseSettledResult<T>, label: string): T | null => {
      if (result.status === "fulfilled") return result.value;
      failures.push(`${label}: ${getErrorMessage(result.reason)}`);
      return null;
    };
    const activation = value(results[0], "Activation");
    const fleet = value(results[1], "Fleet summary");
    const groups = value(results[2], "Anomaly queue");
    const traces = value(results[3], "Recent traces");
    if (!activation && !fleet && !groups && !traces) throw new Error("No operational evidence could be loaded.");
    return {
      activation,
      fleet,
      groups: groups?.groups ?? [],
      traces: traces?.traces ?? [],
      failures,
    } satisfies OverviewData;
  });

  const data = state.data;
  return (
    <>
      <PageHeading
        eyebrow="Fleet signal / live"
        title="Operational overview"
        description="Verify the telemetry path before you act on an anomaly."
        updatedAt={state.updatedAt}
        refresh={() => void reload()}
      />
      {state.status === "loading" && !data && <LoadingBlock />}
      {state.status === "error" && <ErrorBlock error={state.error} retry={() => void reload()} />}
      {data && <OverviewEvidence data={data} />}
    </>
  );
}

function OverviewEvidence({ data }: { data: OverviewData }) {
  const totals = data.fleet?.totals;
  const activationState = data.activation?.state ?? "unknown";
  return (
    <div className="evidence-stack">
      {data.failures.length > 0 && (
        <div className="degraded-banner" role="status">
          <TriangleAlert size={18} aria-hidden="true" />
          <div><strong>Partial evidence</strong><p>{data.failures.join(" · ")}</p></div>
        </div>
      )}
      <section className={`activation-strip activation-strip--${activationState}`} aria-labelledby="activation-title">
        <div className="activation-strip__signal"><Activity aria-hidden="true" /><span /></div>
        <div className="activation-strip__copy">
          <span className="section-kicker">Activation</span>
          <h2 id="activation-title">{activationState === "active" ? "Telemetry is reaching AI Trace" : readable(activationState)}</h2>
          <p>{data.activation?.message ?? (activationState === "active" ? "The fleet has current persisted activity." : "Waiting for the activation contract to report a complete telemetry path.")}</p>
        </div>
        <div className="activation-strip__facts">
          <span><b>{data.activation?.connectedDeployments ?? 0}</b> deployments</span>
          <span><b>{data.activation?.activeSessions ?? totals?.active_sessions ?? 0}</b> live sessions</span>
          <span><b>{formatRelative(data.activation?.lastTelemetryAt)}</b> last signal</span>
        </div>
        <StateBadge value={activationState} />
      </section>

      <section className="summary-band" aria-label="24 hour operational summary">
        <div className="summary-band__lead"><span>24 hour evidence window</span><strong>Fleet posture</strong></div>
        <div><span>Actions observed</span><strong>{(totals?.action_count ?? 0).toLocaleString()}</strong></div>
        <div><span>Error rate</span><strong>{((totals?.error_rate ?? 0) * 100).toFixed(1)}%</strong></div>
        <div><span>Estimated spend</span><strong>{formatUsd(totals?.total_cost_usd)}</strong></div>
        <div><span>Stale excluded</span><strong>{totals?.stale_active_sessions_excluded ?? 0}</strong></div>
      </section>

      <div className="workspace-grid">
        <section className="evidence-panel evidence-panel--wide" aria-labelledby="risk-queue-title">
          <div className="panel-heading">
            <div><span className="section-kicker">Next decision</span><h2 id="risk-queue-title">Anomaly queue</h2></div>
            <Link className="text-link" to="/anomalies">Open inbox <ArrowRight size={15} aria-hidden="true" /></Link>
          </div>
          {data.groups.length === 0 ? (
            <EmptyBlock title="No anomaly groups" body="No anomalies were detected in the last seven days." />
          ) : (
            <AnomalyTable groups={data.groups.slice(0, 5)} />
          )}
        </section>
        <section className="evidence-panel" aria-labelledby="trace-stream-title">
          <div className="panel-heading"><div><span className="section-kicker">Evidence stream</span><h2 id="trace-stream-title">Recent traces</h2></div></div>
          {data.traces.length === 0 ? (
            <EmptyBlock title="No traces yet" body="Instrumented agent runs will appear here." />
          ) : (
            <ul className="trace-stream">
              {data.traces.slice(0, 6).map((trace) => (
                <li key={trace.id}>
                  <Link to={`/traces/${trace.id}`}>
                    <span className={`trace-stream__status trace-stream__status--${trace.status}`} />
                    <span><strong>{readable(trace.trace_type)}</strong><small>{formatRelative(trace.started_at)} · {trace.span_count} spans</small></span>
                    <ArrowRight size={15} aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function AnomalyTable({ groups }: { groups: AnomalyGroup[] }) {
  return (
    <div className="table-scroll">
      <table className="evidence-table">
        <thead><tr><th>Signal</th><th>Severity</th><th>State</th><th>Occurrences</th><th>Last detected</th><th><span className="sr-only">Open</span></th></tr></thead>
        <tbody>
          {groups.map((group) => (
            <tr key={group.fingerprint}>
              <td><Link className="table-primary-link" to={`/anomalies/${group.representative_anomaly_id}`}><span>{group.title}</span><small>{readable(group.anomaly_type)}</small></Link></td>
              <td><StateBadge value={group.representative_severity} /></td>
              <td><span className="data-value">{group.open_count} open</span></td>
              <td><span className="data-value">{group.total_occurrences}</span></td>
              <td><span title={formatDate(group.last_detected_at)}>{formatRelative(group.last_detected_at)}</span></td>
              <td><Link className="row-arrow" to={`/anomalies/${group.representative_anomaly_id}`} aria-label={`Open ${group.title}`}><ArrowRight size={16} aria-hidden="true" /></Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AnomalyInboxPage({ context }: { context: ConsoleContext }) {
  usePageTitle("Anomaly inbox");
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [search, setSearch] = useState("");
  const key = `anomalies:${context.orgId}:${status}:${severity}`;
  const { state, reload } = useResource(key, () => api.anomalies(context.orgId, { status, severity }));
  const visible = state.data?.anomalies.filter((item) => {
    const query = search.trim().toLowerCase();
    return !query || item.title.toLowerCase().includes(query) || item.detector_name.toLowerCase().includes(query);
  }) ?? [];

  return (
    <>
      <PageHeading
        eyebrow="Anomaly signal / triage"
        title="Anomaly inbox"
        description="Start with material risk, then follow its persisted evidence."
        updatedAt={state.updatedAt}
        refresh={() => void reload()}
      />
      <section className="filter-bar" aria-label="Anomaly filters">
        <div className="search-input"><Search size={17} aria-hidden="true" /><label className="sr-only" htmlFor="anomaly-search">Search anomalies</label><input id="anomaly-search" placeholder="Search signal or detector" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
        <ListFilter size={17} aria-hidden="true" />
        <label htmlFor="status-filter">Status</label>
        <div className="select-wrap"><select id="status-filter" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All states</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option></select><ChevronDown size={14} aria-hidden="true" /></div>
        <label htmlFor="severity-filter">Severity</label>
        <div className="select-wrap"><select id="severity-filter" value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select><ChevronDown size={14} aria-hidden="true" /></div>
      </section>
      {state.status === "loading" && !state.data && <LoadingBlock label="Loading anomaly inbox" />}
      {state.status === "error" && <ErrorBlock error={state.error} retry={() => void reload()} />}
      {state.data && visible.length === 0 && <EmptyBlock title={search ? "No matching anomalies" : "Inbox is clear"} body={search ? "Change the search or filters to widen the evidence set." : "No anomalies were found in this 30-day window."} />}
      {state.data && visible.length > 0 && (
        <section className="evidence-panel evidence-panel--flush" aria-label="Anomaly results">
          <div className="result-count"><span>{visible.length} shown</span><span>{state.data.total} total</span></div>
          <div className="table-scroll">
            <table className="evidence-table evidence-table--inbox">
              <thead><tr><th>Signal</th><th>Severity</th><th>Status</th><th>Detector</th><th>Detected</th><th><span className="sr-only">Open</span></th></tr></thead>
              <tbody>
                {visible.map((anomaly) => (
                  <tr key={anomaly.id}>
                    <td><Link className="table-primary-link" to={`/anomalies/${anomaly.id}`}><span>{anomaly.title}</span><small>{readable(anomaly.anomaly_type)}</small></Link></td>
                    <td><StateBadge value={anomaly.severity} /></td><td><StateBadge value={anomaly.status} /></td>
                    <td>{anomaly.detector_name}</td><td>{formatRelative(anomaly.detected_at)}</td>
                    <td><Link className="row-arrow" to={`/anomalies/${anomaly.id}`} aria-label={`Investigate ${anomaly.title}`}><ArrowRight size={16} aria-hidden="true" /></Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function AnomalyDetailPage({ context }: { context: ConsoleContext }) {
  const { anomalyId = "" } = useParams();
  const navigate = useNavigate();
  const { state, reload } = useResource(`anomaly:${context.orgId}:${anomalyId}`, () => api.anomaly(context.orgId, anomalyId));
  const [note, setNote] = useState("");
  const [mutating, setMutating] = useState(false);
  const [mutationMessage, setMutationMessage] = useState<string | null>(null);
  const canOperate = hasOperatorRole(context.session.roles);
  usePageTitle(state.data?.title ?? "Anomaly detail");

  async function transition(status: "acknowledged" | "resolved" | "open") {
    setMutating(true);
    setMutationMessage(null);
    try {
      await api.updateAnomaly(context.orgId, anomalyId, context.session.csrf_token, {
        status,
        note: note.trim() || undefined,
      });
      setNote("");
      setMutationMessage(`Anomaly marked ${status}.`);
      await reload();
    } catch (error) {
      setMutationMessage(getErrorMessage(error));
    } finally {
      setMutating(false);
    }
  }

  return (
    <>
      <button className="back-link" type="button" onClick={() => navigate("/anomalies")}><ArrowLeft size={16} aria-hidden="true" /> Anomaly inbox</button>
      {state.status === "loading" && !state.data && <LoadingBlock label="Loading anomaly evidence" />}
      {state.status === "error" && <ErrorBlock error={state.error} retry={() => void reload()} />}
      {state.data && (
        <AnomalyDetail
          anomaly={state.data}
          canOperate={canOperate}
          note={note}
          setNote={setNote}
          mutating={mutating}
          transition={transition}
          message={mutationMessage}
        />
      )}
    </>
  );
}

function AnomalyDetail({
  anomaly,
  canOperate,
  note,
  setNote,
  mutating,
  transition,
  message,
}: {
  anomaly: Anomaly;
  canOperate: boolean;
  note: string;
  setNote: (note: string) => void;
  mutating: boolean;
  transition: (status: "acknowledged" | "resolved" | "open") => Promise<void>;
  message: string | null;
}) {
  return (
    <article className="detail-layout">
      <header className="incident-heading">
        <div className="incident-heading__meta"><StateBadge value={anomaly.severity} /><StateBadge value={anomaly.status} /><span className="mono">{anomaly.id}</span></div>
        <p className="eyebrow">{readable(anomaly.anomaly_type)}</p>
        <h1>{anomaly.title}</h1>
        <p>{anomaly.description ?? "The detector did not attach a narrative. Use the measurements and linked trace to verify impact."}</p>
      </header>

      <div className="detail-grid">
        <div className="detail-main">
          <section className="evidence-panel" aria-labelledby="measurements-title">
            <div className="panel-heading"><div><span className="section-kicker">Observed evidence</span><h2 id="measurements-title">Measurements</h2></div></div>
            <dl className="measurements">
              <div><dt>Observed</dt><dd>{anomaly.observed_value ?? "—"}</dd></div>
              <div><dt>Baseline</dt><dd>{anomaly.baseline_value ?? "—"}</dd></div>
              <div><dt>Deviation</dt><dd>{anomaly.deviation_ratio === null ? "—" : `${(anomaly.deviation_ratio * 100).toFixed(1)}%`}</dd></div>
              <div><dt>Detector score</dt><dd>{anomaly.score ?? "—"}</dd></div>
            </dl>
            <div className="provenance-row"><ServerCog size={17} aria-hidden="true" /><span>Produced by</span><strong>{anomaly.detector_name}</strong><span>{formatDate(anomaly.detected_at)}</span></div>
          </section>

          <section className="evidence-panel" aria-labelledby="causal-title">
            <div className="panel-heading"><div><span className="section-kicker">Causal path</span><h2 id="causal-title">Runtime pulse</h2></div></div>
            <ol className="evidence-timeline">
              <li className="evidence-timeline__item evidence-timeline__item--complete"><span className="timeline-node"><Check size={15} aria-hidden="true" /></span><div><strong>Signal detected</strong><p>{formatDate(anomaly.detected_at)}</p></div></li>
              <li className={`evidence-timeline__item ${anomaly.acknowledged_at ? "evidence-timeline__item--complete" : ""}`}><span className="timeline-node"><UserRoundCheck size={15} aria-hidden="true" /></span><div><strong>Operator acknowledged</strong><p>{formatDate(anomaly.acknowledged_at)}</p></div></li>
              <li className={`evidence-timeline__item ${anomaly.trace_id ? "evidence-timeline__item--linked" : ""}`}><span className="timeline-node"><Fingerprint size={15} aria-hidden="true" /></span><div><strong>Trace evidence</strong>{anomaly.trace_id ? <Link className="trace-evidence-link" to={`/traces/${anomaly.trace_id}`}><span className="mono">{anomaly.trace_id}</span><ArrowRight size={15} aria-hidden="true" /></Link> : <p>No trace was linked to this anomaly.</p>}</div></li>
              <li className={`evidence-timeline__item ${anomaly.resolved_at ? "evidence-timeline__item--complete" : ""}`}><span className="timeline-node"><CheckCircle2 size={15} aria-hidden="true" /></span><div><strong>Risk resolved</strong><p>{formatDate(anomaly.resolved_at)}</p></div></li>
            </ol>
          </section>
        </div>

        <aside className="operator-panel" aria-labelledby="operator-action-title">
          <span className="section-kicker">Controlled mutation</span>
          <h2 id="operator-action-title">Operator action</h2>
          {canOperate ? (
            <>
              <label className="field-label" htmlFor="operator-note">Evidence note <span>optional</span></label>
              <textarea id="operator-note" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Record what you verified" rows={4} />
              <div className="operator-actions">
                {anomaly.status === "open" && <button className="button button--primary" type="button" disabled={mutating} onClick={() => void transition("acknowledged")}><UserRoundCheck size={16} aria-hidden="true" /> Acknowledge</button>}
                {anomaly.status !== "resolved" && <button className="button button--success" type="button" disabled={mutating} onClick={() => void transition("resolved")}><CheckCircle2 size={16} aria-hidden="true" /> Mark resolved</button>}
                {anomaly.status === "resolved" && <button className="button button--quiet" type="button" disabled={mutating} onClick={() => void transition("open")}><RotateCcw size={16} aria-hidden="true" /> Reopen</button>}
              </div>
              <p className="control-note"><ShieldCheck size={15} aria-hidden="true" /> Changes are CSRF-protected and attributed to {anomaly.updated_by ?? "your session"}.</p>
            </>
          ) : (
            <div className="viewer-notice"><ShieldCheck size={20} aria-hidden="true" /><strong>Viewer access</strong><p>You can inspect all evidence. An operator or administrator must change anomaly state.</p></div>
          )}
          {message && <p className="mutation-message" role="status" aria-live="polite">{message}</p>}
          {anomaly.note && <div className="latest-note"><span>Latest note</span><p>{anomaly.note}</p></div>}
        </aside>
      </div>
    </article>
  );
}

function TraceDetailPage({ context }: { context: ConsoleContext }) {
  const { traceId = "" } = useParams();
  const navigate = useNavigate();
  const { state, reload } = useResource(`trace:${context.orgId}:${traceId}`, () => api.trace(context.orgId, traceId));
  usePageTitle("Trace evidence");

  return (
    <>
      <button className="back-link" type="button" onClick={() => navigate(-1)}><ArrowLeft size={16} aria-hidden="true" /> Back to investigation</button>
      {state.status === "loading" && !state.data && <LoadingBlock label="Loading trace evidence" />}
      {state.status === "error" && <ErrorBlock error={state.error} retry={() => void reload()} />}
      {state.data && <TraceDetailView trace={state.data} />}
    </>
  );
}

function TraceDetailView({ trace }: { trace: TraceDetail }) {
  return (
    <article className="detail-layout trace-detail">
      <header className="incident-heading">
        <div className="incident-heading__meta"><StateBadge value={trace.status} /><span>{trace.spans.length} spans</span><span className="mono">{trace.id}</span></div>
        <p className="eyebrow">Linked trace evidence</p>
        <h1>{readable(trace.trace_type)}</h1>
        <p>Inspect the execution sequence, provider boundaries, and failures captured for this run.</p>
      </header>
      <section className="summary-band summary-band--trace" aria-label="Trace summary">
        <div className="summary-band__lead"><span>Started</span><strong>{formatDate(trace.started_at)}</strong></div>
        <div><span>Duration</span><strong>{formatDuration(trace.duration_ms)}</strong></div>
        <div><span>Input tokens</span><strong>{trace.total_input_tokens.toLocaleString()}</strong></div>
        <div><span>Output tokens</span><strong>{trace.total_output_tokens.toLocaleString()}</strong></div>
        <div><span>Estimated cost</span><strong>{formatUsd(trace.estimated_cost_usd)}</strong></div>
      </section>
      {trace.error_message && <div className="degraded-banner degraded-banner--fault"><XCircle size={18} aria-hidden="true" /><div><strong>Trace failed</strong><p>{trace.error_message}</p></div></div>}
      <section className="evidence-panel" aria-labelledby="execution-title">
        <div className="panel-heading"><div><span className="section-kicker">Ordered evidence</span><h2 id="execution-title">Execution sequence</h2></div><span className="mono">{trace.correlation_id}</span></div>
        {trace.spans.length === 0 ? (
          <EmptyBlock title="No spans recorded" body="The trace exists, but no child execution spans were persisted." />
        ) : (
          <ol className="span-timeline">
            {trace.spans.map((span, index) => (
              <li key={span.id} className="span-row">
                <span className={`span-node span-node--${span.status}`}>{String(index + 1).padStart(2, "0")}</span>
                <div className="span-card">
                  <div className="span-card__heading"><div><span className="section-kicker">{readable(span.span_type)}</span><h3>{span.name}</h3></div><StateBadge value={span.status} /></div>
                  <div className="span-facts"><span><Clock3 size={14} aria-hidden="true" /> {formatDuration(span.duration_ms)}</span><span>{span.input_tokens + span.output_tokens} tokens</span>{span.provider && <span>{span.provider}{span.model ? ` / ${span.model}` : ""}</span>}<span className="mono">{span.id}</span></div>
                  {span.error_message && <p className="span-error">{span.error_message}</p>}
                  {span.reasoning_steps.length > 0 && (
                    <details className="reasoning-disclosure">
                      <summary>{span.reasoning_steps.length} recorded reasoning steps</summary>
                      <ol>{span.reasoning_steps.map((step) => <li key={step.id}><span className="reasoning-number">{step.step_number}</span><div><strong>{readable(step.step_type)}</strong><p>{step.description}</p>{step.explanation && <p className="reasoning-explanation">{step.explanation}</p>}</div></li>)}</ol>
                    </details>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
      <footer className="trace-integrity"><ShieldCheck size={18} aria-hidden="true" /><div><strong>Evidence boundary</strong><p>Prompt and response content is excluded from this operator view. IDs, timing, status, and token measurements remain available for verification.</p></div></footer>
    </article>
  );
}

function AppRoutes({
  session,
  orgId,
  setOrgId,
  signOut,
}: {
  session: SessionMetadata;
  orgId: string | null;
  setOrgId: (orgId: string) => void;
  signOut: () => Promise<void>;
}) {
  if (!orgId) {
    return (
      <Routes>
        <Route path="/select-org" element={<OrgSelectionPage session={session} choose={setOrgId} signOut={signOut} />} />
        <Route path="*" element={<Navigate to="/select-org" replace />} />
      </Routes>
    );
  }

  const context = { session, orgId, setOrgId, signOut };
  return (
    <Routes>
      <Route element={<ConsoleShell context={context} />}>
        <Route path="/overview" element={<OverviewPage context={context} />} />
        <Route path="/anomalies" element={<AnomalyInboxPage context={context} />} />
        <Route path="/anomalies/:anomalyId" element={<AnomalyDetailPage context={context} />} />
        <Route path="/traces/:traceId" element={<TraceDetailPage context={context} />} />
      </Route>
      <Route path="/select-org" element={<OrgSelectionPage session={session} choose={setOrgId} signOut={signOut} />} />
      <Route path="*" element={<Navigate to="/overview" replace />} />
    </Routes>
  );
}

export function App() {
  const [session, setSession] = useState<SessionMetadata | null>(null);
  const [orgId, setOrgIdState] = useState<string | null>(storedOrganization);
  const [booting, setBooting] = useState(true);
  const setOrgId = useCallback((nextOrgId: string) => {
    persistOrganization(nextOrgId);
    setOrgIdState(nextOrgId);
  }, []);

  useEffect(() => {
    let active = true;
    api.session()
      .then((metadata) => {
        if (active) setSession(metadata);
      })
      .catch(() => {
        if (active) setSession(null);
      })
      .finally(() => {
        if (active) setBooting(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (orgId && session && !session.org_ids.includes(orgId)) {
      persistOrganization(null);
      setOrgIdState(null);
    }
  }, [orgId, session]);

  async function signOut() {
    if (session) {
      try {
        await api.logout(session.csrf_token);
      } finally {
        setSession(null);
        persistOrganization(null);
        setOrgIdState(null);
      }
    }
  }

  if (booting) {
    return <main className="boot-screen"><div className="brand-lockup"><span className="brand-glyph">A/T</span><span>AI Trace</span></div><LoadingBlock label="Restoring protected session" /></main>;
  }

  if (!session) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage onLogin={(metadata) => { setSession(metadata); persistOrganization(null); setOrgIdState(null); }} />} />
      </Routes>
    );
  }

  return <AppRoutes session={session} orgId={orgId} setOrgId={setOrgId} signOut={signOut} />;
}
