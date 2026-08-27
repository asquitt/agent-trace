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

  it("normalizes activation response variants at the API boundary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          status: "healthy",
          deployment_count: 3,
          session_count: 8,
          last_event_at: "2026-08-27T17:00:00Z",
          missing_requirements: [],
        }),
      ),
    );

    await expect(api.activation("northstar")).resolves.toEqual({
      state: "active",
      connectedDeployments: 3,
      activeSessions: 8,
      lastTelemetryAt: "2026-08-27T17:00:00Z",
      message: null,
      missingSignals: [],
    });
  });
});
