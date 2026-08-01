import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  it("shows the message and fires retry", () => {
    const onRetry = vi.fn();
    render(<ErrorState message="API returned 429" onRetry={onRetry} />);
    expect(screen.getByText(/429/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalled();
  });
});
