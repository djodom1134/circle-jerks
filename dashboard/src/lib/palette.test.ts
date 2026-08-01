import { describe, expect, it } from "vitest";
import { localityColor, typeColor, LOCALITY_LABEL, TYPE_LABEL } from "./palette";

describe("palette", () => {
  it("gives the validated locality hues in light mode", () => {
    expect(localityColor("local", false)).toBe("#2a78d6");
    expect(localityColor("out_of_town", false)).toBe("#eb6834");
    expect(localityColor("unclassified", false)).toBe("#b9b7ae");
  });
  it("gives dark-stepped locality hues in dark mode", () => {
    expect(localityColor("local", true)).toBe("#3987e5");
  });
  it("labels are human-readable", () => {
    expect(LOCALITY_LABEL.out_of_town).toBe("Out of town");
    expect(TYPE_LABEL.touch_and_go).toBe("Touch & go");
  });
});
