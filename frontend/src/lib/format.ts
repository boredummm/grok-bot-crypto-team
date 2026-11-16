export function money(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return String(value);
  return num.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

export function compact(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return String(value);
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(num);
}

export function signed(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return String(value);
  const text = money(Math.abs(num));
  if (num > 0) return `+${text}`;
  if (num < 0) return `-${text}`;
  return text;
}

export function pct(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return String(value);
  return `${num.toFixed(2)}%`;
}

export function shortMint(mint: string): string {
  if (mint.length <= 16) return mint;
  return `${mint.slice(0, 6)}…${mint.slice(-4)}`;
}

export function tone(value: string | number | null | undefined): "up" | "down" | "flat" {
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num) || num === 0) return "flat";
  return num > 0 ? "up" : "down";
}
