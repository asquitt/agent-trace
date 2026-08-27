import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("operator journey", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("clears the API key after exchange and shows session organizations", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(
        jsonResponse({
          csrf_token: "csrf-token",
          subject: "operator@example.com",
          roles: ["operator"],
          org_ids: ["northstar", "sandbox"],
          expires_at: "2026-08-27T18:00:00Z",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/login"]}>
        <App />
      </MemoryRouter>,
    );

    const keyInput = await screen.findByLabelText("Operator API key");
    await user.type(keyInput, "sensitive-key");
    await user.click(screen.getByRole("button", { name: "Continue to organizations" }));

    expect(await screen.findByRole("heading", { name: "Which fleet are you investigating?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /northstar/i })).toBeInTheDocument();
    expect(screen.queryByDisplayValue("sensitive-key")).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    await user.click(screen.getByRole("button", { name: /northstar/i }));
    expect(await screen.findByRole("heading", { name: "Operational overview" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("ai-trace.selected-org")).toBe("northstar");
  });

  it("restores the selected organization for a deep evidence link", async () => {
    window.sessionStorage.setItem("ai-trace.selected-org", "northstar");
    const session = {
      csrf_token: "csrf-token",
      subject: "operator@example.com",
      roles: ["operator"],
      org_ids: ["northstar"],
      expires_at: "2026-08-27T18:00:00Z",
    };
    const trace = {
      id: "trace-123",
      correlation_id: "correlation-123",
      trace_type: "ranking",
      status: "completed",
      started_at: "2026-08-27T17:00:00Z",
      completed_at: "2026-08-27T17:00:01Z",
      duration_ms: 1000,
      total_input_tokens: 10,
      total_output_tokens: 5,
      estimated_cost_usd: 0.001,
      tags: [],
      idea_id: null,
      ranking_id: null,
      error_message: null,
      metadata: {},
      spans: [],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(session))
      .mockResolvedValueOnce(jsonResponse(trace));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/traces/trace-123"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Ranking" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/traces/trace-123?include_prompts=false",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });
});
