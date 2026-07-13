import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { ZERO_LEDGER_FIXTURE } from "./test/fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  } as Response;
}

describe("App", () => {
  it("renders a loading state before the fetch resolves", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})), // never resolves
    );

    render(<App />);

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText(/counting runway uses/i)).toBeTruthy();
  });

  it("renders an error state when the fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network unreachable"))),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/couldn't load the runway-use ledger/i)).toBeTruthy();
    expect(screen.getByText(/network unreachable/i)).toBeTruthy();
  });

  it("renders an error state when the API returns a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({}, false, 500))),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });

  it("renders a deliberate zero state -- not fabricated data -- when the API returns all zeroes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(ZERO_LEDGER_FIXTURE))),
    );

    render(<App />);

    // Note: native <output> elements (in the calculator) also carry an
    // implicit role="status", so wait on the loading copy specifically
    // rather than the role, which is not unique once the page is ready.
    await waitFor(() => expect(screen.queryByText(/counting runway uses/i)).toBeNull());

    // The deliberate zero banner explaining the zero, not a broken UI.
    expect(screen.getByText(/shown deliberately/i)).toBeTruthy();
    // The real headline number is rendered as 0, not hidden or replaced with a placeholder.
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
    // The operator ledger explains there is nothing to show, rather than an empty table.
    expect(screen.getByText(/no operators recorded/i)).toBeTruthy();
  });
});
