import { SeatName } from "../api";

export const SEAT_COLORS: Record<SeatName, string> = {
  CHIEF: "#38f8ff",
  SCAN: "#f8fafc",
  VET: "#ff384c",
  SOCIAL: "#2979ff",
  SIZE: "#00e676",
  FILLS: "#ff2e93",
  RISK: "#ffab00",
  CRAWLER: "#ff4da6",
};

export function SeatSprite({
  seat,
  size = 26,
  orbit = false,
}: {
  seat: SeatName;
  size?: number;
  orbit?: boolean;
}) {
  if (seat === "CHIEF") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true">
        {/* Headphone arch */}
        <rect x="4" y="3" width="16" height="2" fill="#2b3348" />
        <rect x="2" y="5" width="3" height="10" fill="#1b2232" />
        <rect x="19" y="5" width="3" height="10" fill="#1b2232" />
        <rect x="2" y="8" width="2" height="5" fill="#38f8ff" />
        <rect x="20" y="8" width="2" height="5" fill="#38f8ff" />
        {/* White pixel head */}
        <rect x="6" y="5" width="12" height="14" fill="#f4f7fb" />
        <rect x="5" y="7" width="14" height="10" fill="#f4f7fb" />
        {/* Dark visor + eyes */}
        <rect x="7" y="9" width="10" height="5" fill="#0d111a" />
        <rect x="9" y="10" width="2" height="3" fill="#38f8ff" />
        <rect x="13" y="10" width="2" height="3" fill="#38f8ff" />
      </svg>
    );
  }

  if (seat === "SCAN") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true">
        {orbit && <rect x="1" y="11" width="2" height="2" fill="#f8fafc" opacity="0.85" />}
        {orbit && <rect x="21" y="8" width="2" height="2" fill="#38f8ff" opacity="0.85" />}
        <rect x="7" y="4" width="10" height="16" fill="#f8fafc" />
        <rect x="5" y="6" width="14" height="12" fill="#f8fafc" />
        <rect x="4" y="8" width="16" height="8" fill="#f8fafc" />
        <rect x="9" y="9" width="2" height="4" fill="#0c1018" />
        <rect x="13" y="9" width="2" height="4" fill="#0c1018" />
      </svg>
    );
  }

  if (seat === "VET") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true">
        {orbit && <rect x="1" y="6" width="2" height="2" fill="#ff384c" opacity="0.85" />}
        {orbit && <rect x="21" y="15" width="2" height="2" fill="#ff7582" opacity="0.85" />}
        <rect x="5" y="5" width="14" height="14" fill="#ff384c" />
        <rect x="6" y="4" width="12" height="16" fill="#ff384c" />
        <rect x="4" y="6" width="16" height="12" fill="#ff384c" />
        <rect x="6" y="5" width="10" height="2" fill="#ff6b7a" />
        <rect x="9" y="9" width="2" height="4" fill="#1c0508" />
        <rect x="13" y="9" width="2" height="4" fill="#1c0508" />
      </svg>
    );
  }

  if (seat === "SOCIAL") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true">
        {orbit && <rect x="2" y="16" width="2" height="2" fill="#5ea0ff" opacity="0.85" />}
        {orbit && <rect x="20" y="6" width="2" height="2" fill="#2979ff" opacity="0.85" />}
        <rect x="7" y="4" width="10" height="16" fill="#2979ff" />
        <rect x="5" y="6" width="14" height="12" fill="#2979ff" />
        <rect x="4" y="8" width="16" height="8" fill="#2979ff" />
        <rect x="7" y="5" width="6" height="2" fill="#6eabff" />
        <rect x="9" y="9" width="2" height="4" fill="#06122b" />
        <rect x="13" y="9" width="2" height="4" fill="#06122b" />
      </svg>
    );
  }

  if (seat === "SIZE") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true">
        {orbit && <rect x="1" y="9" width="2" height="2" fill="#00e676" opacity="0.85" />}
        {orbit && <rect x="21" y="14" width="2" height="2" fill="#69f0ae" opacity="0.85" />}
        <rect x="5" y="7" width="14" height="10" fill="#00e676" />
        <rect x="3" y="9" width="18" height="6" fill="#00e676" />
        <rect x="6" y="8" width="10" height="2" fill="#69f0ae" />
        <rect x="9" y="10" width="2" height="3" fill="#042111" />
        <rect x="13" y="10" width="2" height="3" fill="#042111" />
      </svg>
    );
  }

  if (seat === "FILLS") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true">
        {orbit && <rect x="2" y="15" width="2" height="2" fill="#ff2e93" opacity="0.85" />}
        {orbit && <rect x="20" y="9" width="2" height="2" fill="#ff80bf" opacity="0.85" />}
        <rect x="11" y="4" width="2" height="2" fill="#ff2e93" />
        <rect x="9" y="6" width="6" height="3" fill="#ff2e93" />
        <rect x="7" y="9" width="10" height="3" fill="#ff2e93" />
        <rect x="5" y="12" width="14" height="3" fill="#ff2e93" />
        <rect x="4" y="15" width="16" height="4" fill="#ff2e93" />
        <rect x="9" y="12" width="2" height="3" fill="#290416" />
        <rect x="13" y="12" width="2" height="3" fill="#290416" />
      </svg>
    );
  }

  if (seat === "RISK") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true">
        {orbit && <rect x="2" y="7" width="2" height="2" fill="#ffab00" opacity="0.85" />}
        {orbit && <rect x="20" y="16" width="2" height="2" fill="#ffd740" opacity="0.85" />}
        <rect x="8" y="4" width="8" height="16" fill="#ffab00" />
        <rect x="6" y="6" width="12" height="12" fill="#ffab00" />
        <rect x="4" y="8" width="16" height="8" fill="#ffab00" />
        <rect x="8" y="5" width="8" height="2" fill="#ffd740" />
        <rect x="9" y="10" width="2" height="4" fill="#261700" />
        <rect x="13" y="10" width="2" height="4" fill="#261700" />
      </svg>
    );
  }

  // CRAWLER monitor glyph
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true">
      <rect x="4" y="5" width="16" height="14" fill="#191326" stroke="#ff4da6" strokeWidth="2" />
      <rect x="7" y="9" width="4" height="2" fill="#38f8ff" />
      <rect x="12" y="9" width="5" height="2" fill="#ff4da6" />
      <rect x="7" y="13" width="10" height="2" fill="#00e676" />
    </svg>
  );
}

export function GmgnEmblem({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" shapeRendering="crispEdges" aria-hidden="true">
      {/* Crisp geometric pixel GMGN mark — emerald & cyan twin candlesticks + visor */}
      <rect x="2" y="2" width="24" height="24" fill="#0a1514" stroke="#00e676" strokeWidth="2" />
      <rect x="6" y="7" width="6" height="14" fill="#00e676" />
      <rect x="8" y="10" width="4" height="8" fill="#0a1514" />
      <rect x="16" y="7" width="6" height="14" fill="#38f8ff" />
      <rect x="16" y="10" width="4" height="8" fill="#0a1514" />
      <rect x="12" y="12" width="4" height="4" fill="#ff2e93" />
    </svg>
  );
}
