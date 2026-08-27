import { describe, expect, it, vi } from "vitest";

import { api } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("operator API client", () => {
  it("exchanges the API key with same-origin credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        csrf_token: "csrf-token",
        subject: "operator@example.com",
        roles: ["operator"],
        org_ids: ["northstar"],
        expires_at: "2026-08-27T18:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.login("one-time-api-key");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/v1/auth/browser/sessions");
    expect(init.credentials).toBe("same-origin");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ api_key: "one-time-api-key" });
  });

  it("adds tenant and CSRF headers to anomaly mutations", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: "anomaly-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.updateAnomaly("northstar", "anomaly-1", "csrf-token", {
      status: "acknowledged",
    });

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Headers;
    expect(path).toBe("/api/v1/observability/anomalies/anomaly-1");
    expect(init.credentials).toBe("same-origin");
    expect(headers.get("X-Org-Id")).toBe("northstar");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token");
  });

  it("normalizes the backend setup-required activation state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          state: "setup_required",
          connected_deployments: 0,
          active_sessions: 0,
          last_telemetry_at: null,
          message: "Register a deployment to begin activation.",
          missing_signals: ["deployment_registration", "agent_telemetry"],
        }),
      ),
    );

    await expect(api.activation("northstar")).resolves.toEqual({
      state: "inactive",
      connectedDeployments: 0,
      activeSessions: 0,
      lastTelemetryAt: null,
      message: "Register a deployment to begin activation.",
      missingSignals: ["deployment_registration", "agent_telemetry"],
    });
  });

  it("normalizes the backend awaiting-telemetry activation state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          state: "awaiting_telemetry",
          connected_deployments: 0,
          active_sessions: 0,
          last_telemetry_at: null,
          message: "Deployment registered; send an action or trace to complete activation.",
          missing_signals: ["agent_telemetry"],
        }),
      ),
    );

    await expect(api.activation("northstar")).resolves.toEqual({
      state: "waiting",
      connectedDeployments: 0,
      activeSessions: 0,
      lastTelemetryAt: null,
      message: "Deployment registered; send an action or trace to complete activation.",
      missingSignals: ["agent_telemetry"],
    });
  });
});
