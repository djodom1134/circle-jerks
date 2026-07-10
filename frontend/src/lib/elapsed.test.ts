import { describe, it, expect } from "vitest";
import { elapsedLabel } from "./elapsed";

describe("elapsedLabel", () => {
  const now = 1_700_000_000;
  const at = (secondsAgo: number) => elapsedLabel(now - secondsAgo, now);

  it("says 'just now' under a minute", () => {
    expect(at(0)).toBe("just now");
    expect(at(59)).toBe("just now");
  });

  it("uses minutes under an hour", () => {
    expect(at(60)).toBe("1m ago");
    expect(at(42 * 60)).toBe("42m ago");
    expect(at(59 * 60)).toBe("59m ago");
  });

  it("uses hours and minutes under a day", () => {
    expect(at(3600)).toBe("1h ago");
    expect(at(3600 + 12 * 60)).toBe("1h 12m ago");
    // The bug: a 13.8h-old runway change rendered as "826m ago" and looked stuck.
    expect(at(826 * 60)).toBe("13h 46m ago");
  });

  it("uses days and hours beyond a day", () => {
    expect(at(86400)).toBe("1d ago");
    expect(at(86400 + 5 * 3600)).toBe("1d 5h ago");
  });

  it("never renders a negative elapsed time from clock skew", () => {
    expect(elapsedLabel(now + 30, now)).toBe("just now");
  });
});
