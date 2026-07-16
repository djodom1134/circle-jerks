import { describe, expect, it } from "vitest";
import { buildTaglines, pickTagline } from "./taglines";

describe("buildTaglines", () => {
  it("omits the dynamic line when there is no count", () => {
    const list = buildTaglines(null);
    expect(list.length).toBeGreaterThan(0);
    expect(list.some((t) => /logged today/.test(t))).toBe(false);
  });

  it("includes a count-driven line when circles > 0", () => {
    const list = buildTaglines(300);
    expect(list.some((t) => t.includes("300") && /logged today/.test(t))).toBe(true);
  });
});

describe("pickTagline", () => {
  it("is deterministic given rand and always in range", () => {
    const list = ["a", "b", "c"];
    expect(pickTagline(list, 0)).toBe("a");
    expect(pickTagline(list, 0.99)).toBe("c");
    expect(pickTagline([], 0.5)).toBe("");
  });
});
