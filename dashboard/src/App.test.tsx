import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { DAILY_FIXTURE, ORIGINS_FIXTURE, WORST_OFFENDERS_FIXTURE, HOURLY_FIXTURE } from "./fixtures/klmo";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => body } as Response;
}

function stubRoutes() {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/daily-operations")) return Promise.resolve(jsonResponse(DAILY_FIXTURE));
    if (url.includes("/origins")) return Promise.resolve(jsonResponse(ORIGINS_FIXTURE));
    if (url.includes("/worst-offenders")) return Promise.resolve(jsonResponse(WORST_OFFENDERS_FIXTURE));
    if (url.includes("/hourly-profile")) return Promise.resolve(jsonResponse(HOURLY_FIXTURE));
    return Promise.reject(new Error(`unexpected ${url}`));
  }));
}

describe("App", () => {
  it("loads and shows the toolbar and hero chart", async () => {
    stubRoutes();
    render(<App />);
    await waitFor(() => expect(screen.getByRole("region", { name: /filters/i })).toBeTruthy());
    expect(screen.getByRole("img")).toBeTruthy();
  });

  it("shows an error state and can retry when the daily fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500, statusText: "Error", json: async () => ({}) } as Response)));
    render(<App />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("keeps at least one type selected when toggling", async () => {
    stubRoutes();
    render(<App />);
    await waitFor(() => screen.getByRole("region", { name: /filters/i }));
    // Turn off three of four types; the fourth click on the last must be a no-op.
    fireEvent.click(screen.getByRole("button", { name: /landings/i }));
    fireEvent.click(screen.getByRole("button", { name: /touch & go/i }));
    fireEvent.click(screen.getByRole("button", { name: /low approach/i }));
    const takeoffs = screen.getByRole("button", { name: /takeoffs/i });
    fireEvent.click(takeoffs); // last one — guard keeps it on
    expect(takeoffs.getAttribute("aria-pressed")).toBe("true");
  });
});
