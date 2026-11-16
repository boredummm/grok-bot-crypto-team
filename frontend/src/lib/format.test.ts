import { describe, expect, it } from "vitest";
import { money, shortMint, tone } from "./format";

describe("format", () => {
  it("renders dollars and withholds empty values", () => {
    expect(money("1000")).toBe("$1,000.00");
    expect(money(null)).toBe("—");
  });

  it("shortens mint addresses and classifies tone", () => {
    expect(shortMint("demo:momentum")).toBe("demo:momentum");
    expect(shortMint("So11111111111111111111111111111111111111112")).toContain("…");
    expect(tone("-1")).toBe("down");
    expect(tone("2")).toBe("up");
  });
});
