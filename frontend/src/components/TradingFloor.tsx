import { CandidateToken, DemoPlaybackStep, DeskState, FeaturedToken, SeatCard, SeatName } from "../api";
import { compact, money } from "../lib/format";
import { GmgnEmblem, SEAT_COLORS, SeatSprite } from "./PixelSprites";

type Props = {
  desk: DeskState;
  selectedToken: FeaturedToken | null;
  onSelectSeat: (seat: SeatName) => void;
  onSelectToken: (mint: string) => void;
  playbackStep: DemoPlaybackStep | null;
  reducedMotion: boolean;
  showWebOverride: boolean;
};

// Coordinates in %-space (0..100 x 0..100) matching the 6 workstation desks, big screen, center huddle, and CHIEF desk.
const HOME_COORDS: Record<Exclude<SeatName, "CRAWLER">, { x: number; y: number }> = {
  SCAN: { x: 26.5, y: 58.0 },
  SOCIAL: { x: 44.5, y: 58.0 },
  FILLS: { x: 62.5, y: 58.0 },
  SIZE: { x: 26.5, y: 77.5 },
  VET: { x: 44.5, y: 77.5 },
  RISK: { x: 62.5, y: 77.5 },
  CHIEF: { x: 80.8, y: 39.5 },
};

const SCREEN_COORDS: Record<Exclude<SeatName, "CRAWLER">, { x: number; y: number }> = {
  SCAN: { x: 69.5, y: 50.0 },
  VET: { x: 54.0, y: 45.5 },
  SOCIAL: { x: 50.0, y: 55.0 },
  SIZE: { x: 35.0, y: 63.0 },
  FILLS: { x: 59.0, y: 54.0 },
  RISK: { x: 46.0, y: 53.0 },
  CHIEF: { x: 78.5, y: 39.5 },
};

const CENTER_COORDS: Record<Exclude<SeatName, "CRAWLER">, { x: number; y: number }> = {
  SCAN: { x: 55.0, y: 56.5 },
  VET: { x: 45.5, y: 65.5 },
  SOCIAL: { x: 51.5, y: 59.5 },
  SIZE: { x: 33.5, y: 62.0 },
  FILLS: { x: 57.5, y: 58.5 },
  RISK: { x: 48.0, y: 57.0 },
  CHIEF: { x: 79.5, y: 39.5 },
};

function resolveCoords(
  seat: Exclude<SeatName, "CRAWLER">,
  card: SeatCard | undefined,
  playbackStep: DemoPlaybackStep | null,
): { x: number; y: number; bubble: string | null } {
  if (seat === "CHIEF") {
    const bubble =
      playbackStep?.active_seat === "CHIEF"
        ? playbackStep.bubble
        : card?.speech_bubble || null;
    return { ...HOME_COORDS.CHIEF, bubble };
  }
  if (playbackStep) {
    if (playbackStep.active_seat === seat) {
      const targetMap =
        playbackStep.target === "screen"
          ? SCREEN_COORDS
          : playbackStep.target === "center"
            ? CENTER_COORDS
            : HOME_COORDS;
      return { ...targetMap[seat], bubble: playbackStep.bubble };
    }
    return { ...HOME_COORDS[seat], bubble: null };
  }
  const target = card?.target_station || "desk";
  const map = target === "screen" ? SCREEN_COORDS : target === "center" ? CENTER_COORDS : HOME_COORDS;
  return { ...map[seat], bubble: card?.speech_bubble || null };
}

export function TradingFloor({
  desk,
  selectedToken,
  onSelectSeat,
  onSelectToken,
  playbackStep,
  reducedMotion,
  showWebOverride,
}: Props) {
  const token = selectedToken || desk.featured_token;
  const candles = token?.candles || [];
  const seatsByName = Object.fromEntries(desk.seats.map((s) => [s.seat, s])) as Record<SeatName, SeatCard>;

  // Compute candlestick y-scaling inside the big central wall screen (x: 294..756, y: 74..196)
  const prices = candles.flatMap((c) => [c.high, c.low]);
  const minP = prices.length ? Math.min(...prices) : 0.85;
  const maxP = prices.length ? Math.max(...prices) : 1.05;
  const spanP = Math.max(maxP - minP, 0.000001);
  const maxVol = Math.max(...candles.map((c) => c.volume), 1);

  const webActive = showWebOverride || (playbackStep ? playbackStep.crawler_web : desk.crawler_web.active);

  const tickerText = [
    `SOL SWARM FINDS, JEV JUDGES, GMGN PAPER QUOTES`,
    token ? `${token.symbol} ${token.price ? `$${Number(token.price).toFixed(4)}` : "—"}` : "NO ACTIVE TOKEN",
    `PAPER BANK ${money(desk.balance.equity)}`,
    `P&L ${desk.balance.total_pnl ? `$${Number(desk.balance.total_pnl).toFixed(2)}` : "$0.00"}`,
    `OPEN ${desk.balance.open_positions} · HOLDERS ${token?.holder_count ?? "—"}`,
    desk.jev.connected ? `JEV ${desk.jev.resolved_model || "CONNECTED"}` : `JEV NOT CONNECTED`,
    `LIVE SWAPS OFF`,
  ].join("  ·  ");

  const floorAgents: Exclude<SeatName, "CRAWLER">[] = ["SCAN", "VET", "SOCIAL", "SIZE", "FILLS", "RISK", "CHIEF"];

  return (
    <div className="retro-floor-wrap" data-testid="trading-floor">
      {/* Top neon wall ticker */}
      <div className="retro-wall-ticker">
        <span className={reducedMotion ? "" : "retro-ticker-scroll"}>{tickerText}</span>
      </div>

      {/* Main 2D top-down pixel-art SVG room */}
      <div className="retro-floor-stage">
        <svg
          viewBox="0 0 1160 500"
          className="retro-floor-svg"
          shapeRendering="crispEdges"
          role="img"
          aria-label="SOL SWARM + JEV 2D pixel trading control room"
        >
          <defs>
            <pattern id="floorGrid" width="24" height="24" patternUnits="userSpaceOnUse">
              <rect width="24" height="24" fill="#090d16" />
              <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#101726" strokeWidth="1" />
            </pattern>
            <pattern id="wallGrid" width="16" height="16" patternUnits="userSpaceOnUse">
              <rect width="16" height="16" fill="#0d1320" />
              <path d="M 16 0 L 0 0 0 16" fill="none" stroke="#141d30" strokeWidth="1" />
            </pattern>
          </defs>

          {/* Floor & back wall */}
          <rect x="0" y="0" width="1160" height="500" fill="url(#floorGrid)" />
          <rect x="0" y="0" width="1160" height="218" fill="url(#wallGrid)" />
          <rect x="0" y="216" width="1160" height="4" fill="#1d273b" />
          <rect x="0" y="220" width="1160" height="2" fill="#05070c" />

          {/* Left perspective side wall + secondary candlestick monitor */}
          <polygon points="0,0 82,42 82,360 0,430" fill="#0b101b" stroke="#1c2538" strokeWidth="2" />
          <polygon points="12,118 70,92 70,225 12,265" fill="#06090f" stroke="#28354f" strokeWidth="2" />
          {/* Candles on left wall screen */}
          {[0, 1, 2, 3, 4, 5, 6].map((idx) => {
            const cx = 20 + idx * 7;
            const up = idx % 2 === 0 || idx === 5;
            const cy = 195 - idx * 9;
            return (
              <g key={idx}>
                <rect x={cx + 1} y={cy - 8} width="1" height="26" fill={up ? "#00e676" : "#ff384c"} />
                <rect x={cx} y={cy} width="4" height="12" fill={up ? "#00e676" : "#ff384c"} />
              </g>
            );
          })}

          {/* GMGN Wall Sign (replacing FOMO completely) */}
          <g transform="translate(86, 18)">
            <rect x="0" y="0" width="92" height="92" fill="#070c12" stroke="#1e2d3d" strokeWidth="2" />
            <rect x="18" y="12" width="56" height="42" fill="#0a1715" stroke="#00e676" strokeWidth="2" />
            {/* Twin pixel GMGN emblem bars */}
            <rect x="28" y="20" width="12" height="26" fill="#00e676" />
            <rect x="32" y="26" width="8" height="14" fill="#0a1715" />
            <rect x="52" y="20" width="12" height="26" fill="#38f8ff" />
            <rect x="52" y="26" width="8" height="14" fill="#0a1715" />
            <rect x="42" y="29" width="8" height="8" fill="#ff2e93" />
            <text x="46" y="76" textAnchor="middle" fill="#e6f4f1" fontSize="15" fontFamily="Silkscreen, monospace" letterSpacing="3">
              GMGN
            </text>
            <text x="46" y="87" textAnchor="middle" fill="#00e676" fontSize="7" fontFamily="IBM Plex Mono, monospace">
              PAPER VENUE
            </text>
          </g>

          {/* Left GMGN pennant flag + potted plant */}
          <g transform="translate(212, 42)">
            <rect x="-4" y="0" width="54" height="3" fill="#3b4866" />
            <polygon points="0,3 46,3 46,112 23,128 0,112" fill="#0c1220" stroke="#283552" strokeWidth="2" />
            <rect x="13" y="42" width="8" height="20" fill="#00e676" />
            <rect x="25" y="42" width="8" height="20" fill="#38f8ff" />
            <text x="23" y="80" textAnchor="middle" fill="#9fb3c8" fontSize="8" fontFamily="Silkscreen, monospace">
              GMGN
            </text>
          </g>
          <PixelPlant x={180} y={168} />

          {/* Lucky Cat Shelf & Safe Cabinet (left mid) */}
          <g transform="translate(76, 232)">
            {/* Shelf + 4 pixel lucky cats */}
            <rect x="0" y="22" width="76" height="58" fill="#111726" stroke="#29354d" strokeWidth="2" />
            <rect x="6" y="30" width="28" height="42" fill="#0b101a" stroke="#222d42" strokeWidth="1" />
            <rect x="16" y="46" width="8" height="8" fill="#38f8ff" />
            <rect x="40" y="32" width="28" height="16" fill="#0b101a" stroke="#00e676" strokeWidth="1" />
            <text x="54" y="43" textAnchor="middle" fill="#00e676" fontSize="7" fontFamily="Silkscreen, monospace">
              GMGN
            </text>
            {/* 4 mini pixel cats on top of cabinet */}
            {[
              { dx: 8, color: "#00e676" },
              { dx: 24, color: "#ffd740" },
              { dx: 40, color: "#f8fafc" },
              { dx: 56, color: "#ff7043" },
            ].map((cat, idx) => (
              <g key={idx} transform={`translate(${cat.dx}, 6)`}>
                <rect x="1" y="0" width="3" height="3" fill={cat.color} />
                <rect x="8" y="0" width="3" height="3" fill={cat.color} />
                <rect x="1" y="3" width="10" height="12" fill={cat.color} />
                <rect x="3" y="6" width="2" height="2" fill="#090d16" />
                <rect x="7" y="6" width="2" height="2" fill="#090d16" />
                <rect x="4" y="11" width="4" height="2" fill="#ff384c" />
              </g>
            ))}
          </g>
          <PixelPlant x={156} y={266} />

          {/* PAPER HANDS Shredder Bin (bottom-left) */}
          <g transform="translate(34, 368)">
            <rect x="0" y="16" width="68" height="66" fill="#131926" stroke="#2d3952" strokeWidth="2" />
            <rect x="6" y="6" width="56" height="14" fill="#1d2638" stroke="#394766" strokeWidth="1" />
            {/* Shredded paper strips */}
            <rect x="18" y="2" width="6" height="10" fill="#e2e8f0" />
            <rect x="28" y="0" width="6" height="12" fill="#cbd5e1" />
            <rect x="38" y="3" width="6" height="9" fill="#e2e8f0" />
            <rect x="10" y="36" width="48" height="20" fill="#090d16" stroke="#28334a" strokeWidth="1" />
            <text x="34" y="49" textAnchor="middle" fill="#cbd5e1" fontSize="7" fontFamily="Silkscreen, monospace">
              PAPER HANDS
            </text>
          </g>

          {/* HUGE CENTRAL CANDLESTICK WALL MONITOR */}
          <g transform="translate(278, 22)">
            <rect x="0" y="0" width="504" height="182" fill="#060910" stroke="#2e3b56" strokeWidth="3" />
            <rect x="4" y="4" width="496" height="174" fill="#05080f" stroke="#162033" strokeWidth="1" />
            {/* Top metrics bar inside big screen */}
            <rect x="6" y="6" width="492" height="24" fill="#0b111d" />
            <text x="14" y="21" fill="#f8fafc" fontSize="11" fontFamily="Silkscreen, monospace">
              {token ? token.symbol : "NO TOKEN"}
            </text>
            <text x="122" y="21" fill="#8fa3bf" fontSize="9" fontFamily="IBM Plex Mono, monospace">
              MCAP {compact(token?.market_cap ?? null)} · PRICE {token?.price ? `$${Number(token.price).toFixed(4)}` : "—"} · LIQ {compact(token?.liquidity ?? null)} · 5M {token?.change_m5 ? `${token.change_m5}%` : "—"} · TOP1 {token?.top_holder_pct ? `${token.top_holder_pct}%` : "—"}
            </text>

            {/* Horizontal grid lines */}
            {[52, 86, 120, 154].map((gy) => (
              <line key={gy} x1="10" y1={gy} x2="492" y2={gy} stroke="#111a2b" strokeWidth="1" />
            ))}

            {/* Real observed candlesticks */}
            {candles.length > 0 ? (
              candles.map((c, idx) => {
                const stepW = 460 / Math.max(candles.length, 1);
                const cx = 20 + idx * stepW;
                const bw = Math.max(5, Math.min(12, stepW * 0.62));
                const yHigh = 38 + (1 - (c.high - minP) / spanP) * 104;
                const yLow = 38 + (1 - (c.low - minP) / spanP) * 104;
                const yOpen = 38 + (1 - (c.open - minP) / spanP) * 104;
                const yClose = 38 + (1 - (c.close - minP) / spanP) * 104;
                const bodyTop = Math.min(yOpen, yClose);
                const bodyH = Math.max(3, Math.abs(yClose - yOpen));
                const vH = Math.max(2, (c.volume / maxVol) * 22);
                const fill = c.up ? "#00e676" : "#ff384c";
                return (
                  <g key={c.at + idx}>
                    <line x1={cx + bw / 2} y1={yHigh} x2={cx + bw / 2} y2={yLow} stroke={fill} strokeWidth="1.5" />
                    <rect x={cx} y={bodyTop} width={bw} height={bodyH} fill={fill} />
                    <rect x={cx} y={172 - vH} width={bw} height={vH} fill={fill} opacity="0.4" />
                  </g>
                );
              })
            ) : (
              <text x="252" y="102" textAnchor="middle" fill="#64748b" fontSize="11" fontFamily="IBM Plex Mono, monospace">
                Awaiting market observations in {desk.mode.toUpperCase()} mode…
              </text>
            )}

            {/* Real security status callout box inside wall monitor */}
            {token && (
              <g transform="translate(308, 134)">
                <rect x="0" y="0" width="182" height="24" fill="#0d1322" stroke={token.security_status === "pass" ? "#00e676" : "#ff384c"} strokeWidth="1.5" />
                <text x="91" y="15" textAnchor="middle" fill={token.security_status === "pass" ? "#00e676" : "#ff6b7a"} fontSize="9" fontFamily="IBM Plex Mono, monospace">
                  {token.security_status === "pass"
                    ? "authorities renounced · pass"
                    : token.critical_flags[0] || `security: ${token.security_status}`}
                </text>
              </g>
            )}
          </g>

          {/* Under-monitor glowing LED summary strip */}
          <text x="530" y="213" textAnchor="middle" fill="#00e676" fontSize="9.5" fontFamily="IBM Plex Mono, monospace">
            PAPER P&amp;L {desk.balance.total_pnl ? `$${Number(desk.balance.total_pnl).toFixed(2)}` : "$0.00"}  ·  {token ? `$${token.symbol} LIQ ${compact(token.liquidity)}` : "NO PICK"}  ·  BANK {money(desk.balance.equity)}
          </text>

          {/* Right GMGN pennant flag + plant */}
          <g transform="translate(798, 42)">
            <rect x="-4" y="0" width="54" height="3" fill="#3b4866" />
            <polygon points="0,3 46,3 46,112 23,128 0,112" fill="#0c1220" stroke="#283552" strokeWidth="2" />
            <rect x="13" y="42" width="8" height="20" fill="#00e676" />
            <rect x="25" y="42" width="8" height="20" fill="#38f8ff" />
            <text x="23" y="80" textAnchor="middle" fill="#9fb3c8" fontSize="8" fontFamily="Silkscreen, monospace">
              GMGN
            </text>
          </g>
          <PixelPlant x={808} y={168} />

          {/* Wall Clock + DESK MANDATE Board (top right) */}
          <g transform="translate(868, 76)">
            {/* UTC Digital Wall Clock */}
            <rect x="0" y="0" width="98" height="64" fill="#0b101b" stroke="#2c3954" strokeWidth="2" />
            <rect x="8" y="8" width="82" height="32" fill="#060910" stroke="#1b263b" strokeWidth="1" />
            <text x="49" y="29" textAnchor="middle" fill="#00e676" fontSize="14" fontFamily="Silkscreen, monospace">
              {desk.clock_utc.replace(" UTC", "")}
            </text>
            <text x="49" y="54" textAnchor="middle" fill="#94a3b8" fontSize="8" fontFamily="Silkscreen, monospace">
              UTC CLOCK
            </text>

            {/* Mandate Bulletin Board */}
            <rect x="112" y="-12" width="162" height="114" fill="#0d1320" stroke="#2e3b56" strokeWidth="2" />
            <rect x="148" y="-18" width="90" height="14" fill="#182236" stroke="#ff2e93" strokeWidth="1" />
            <text x="193" y="-8" textAnchor="middle" fill="#f8fafc" fontSize="7.5" fontFamily="Silkscreen, monospace">
              DESK MANDATE
            </text>
            {[
              "· CODE FETCHES, JEV JUDGES",
              "· PAPER BANK ONLY ($1,000)",
              "· RISK ENGINE FINAL VETO",
              "· THIN POOL, SMALL TICKET",
              "· CRAWLER READS FIRST",
              "· LIVE SWAPS DISABLED",
            ].map((rule, rIdx) => (
              <text key={rIdx} x="120" y={12 + rIdx * 14} fill={rIdx === 5 ? "#ff6b7a" : "#cbd5e1"} fontSize="7.8" fontFamily="IBM Plex Mono, monospace">
                {rule}
              </text>
            ))}
          </g>

          {/* HEAD OF THE FLOOR (CHIEF) Executive Desk (right middle) */}
          <g transform="translate(856, 206)" onClick={() => onSelectSeat("CHIEF")} style={{ cursor: "pointer" }}>
            <rect x="0" y="14" width="176" height="48" fill="#141c2c" stroke="#303e5c" strokeWidth="2" />
            <rect x="6" y="20" width="164" height="8" fill="#1d283d" />
            {/* Desk monitors & lamp */}
            <rect x="16" y="0" width="34" height="20" fill="#080d16" stroke="#38f8ff" strokeWidth="1.5" />
            <rect x="20" y="6" width="24" height="3" fill="#38f8ff" />
            <rect x="20" y="12" width="16" height="3" fill="#00e676" />
            <rect x="128" y="4" width="28" height="16" fill="#080d16" stroke="#2c3a57" strokeWidth="1.5" />
            {/* Nameplate */}
            <rect x="44" y="34" width="88" height="14" fill="#0b101b" stroke="#38f8ff" strokeWidth="1" />
            <text x="88" y="44" textAnchor="middle" fill="#38f8ff" fontSize="7" fontFamily="Silkscreen, monospace">
              HEAD OF THE FLOOR
            </text>
          </g>

          {/* SIX CENTRAL WORKSTATION DESKS (3 Upper + 3 Lower) */}
          <WorkstationDesk x={244} y={240} label="SCAN · TAPE" color="#38f8ff" kind="wave" onClick={() => onSelectSeat("SCAN")} />
          <WorkstationDesk x={448} y={240} label="SOCIAL · X" color="#2979ff" kind="bars" onClick={() => onSelectSeat("SOCIAL")} />
          <WorkstationDesk x={652} y={240} label="FILLS · QUOTE" color="#ff2e93" kind="bars" onClick={() => onSelectSeat("FILLS")} />

          <WorkstationDesk x={244} y={338} label="SIZE · BANK" color="#00e676" kind="wave" onClick={() => onSelectSeat("SIZE")} />
          <WorkstationDesk x={448} y={338} label="VET · CHAIN" color="#ff384c" kind="bars" onClick={() => onSelectSeat("VET")} />
          <WorkstationDesk x={652} y={338} label="RISK · VETO" color="#ffab00" kind="grid" onClick={() => onSelectSeat("RISK")} />

          {/* Bottom-Center Lounge: Couch, Coffee Table, GMGN Laptop, Twin Plants */}
          <PixelPlant x={392} y={426} />
          <g transform="translate(432, 424)">
            {/* 3-cushion dark pixel sofa */}
            <rect x="0" y="0" width="168" height="44" fill="#141b29" stroke="#2c3852" strokeWidth="2" />
            <rect x="6" y="6" width="50" height="30" fill="#1b2436" />
            <rect x="59" y="6" width="50" height="30" fill="#1b2436" />
            <rect x="112" y="6" width="50" height="30" fill="#1b2436" />
            {/* Coffee table + mini GMGN laptop */}
            <rect x="34" y="48" width="100" height="24" fill="#0e1420" stroke="#28344d" strokeWidth="2" />
            <rect x="70" y="51" width="28" height="16" fill="#070c14" stroke="#00e676" strokeWidth="1" />
            <text x="84" y="62" textAnchor="middle" fill="#00e676" fontSize="6" fontFamily="Silkscreen, monospace">
              GMGN
            </text>
          </g>
          <PixelPlant x={614} y={426} />

          {/* TOP RUNNER Pixel Rocket on Stand (right lower-mid) */}
          <g transform="translate(888, 286)">
            <rect x="8" y="68" width="76" height="26" fill="#121826" stroke="#2f3d59" strokeWidth="2" />
            <text x="46" y="84" textAnchor="middle" fill="#e2e8f0" fontSize="7.5" fontFamily="Silkscreen, monospace">
              TOP RUNNER
            </text>
            {/* Rocket body */}
            <rect x="38" y="10" width="16" height="44" fill="#e2e8f0" />
            <rect x="42" y="2" width="8" height="8" fill="#ff2e93" />
            <rect x="42" y="22" width="8" height="10" fill="#38f8ff" />
            <polygon points="38,40 28,56 38,54" fill="#94a3b8" />
            <polygon points="54,40 64,56 54,54" fill="#94a3b8" />
            <rect x="41" y="54" width="10" height="14" fill="#ffab00" opacity="0.85" />
          </g>

          {/* WHALE RADAR Parabolic Dish (right bottom) */}
          <g transform="translate(886, 394)">
            <rect x="6" y="68" width="84" height="24" fill="#121826" stroke="#2f3d59" strokeWidth="2" />
            <text x="48" y="83" textAnchor="middle" fill="#e2e8f0" fontSize="7.5" fontFamily="Silkscreen, monospace">
              WHALE RADAR
            </text>
            <ellipse cx="48" cy="36" rx="28" ry="22" fill="#182234" stroke="#47597a" strokeWidth="2" />
            <ellipse cx="48" cy="36" rx="16" ry="12" fill="#0e1524" stroke="#38f8ff" strokeWidth="1" />
            <line x1="48" y1="36" x2="66" y2="20" stroke="#00e676" strokeWidth="2" />
            <rect x="64" y="18" width="5" height="5" fill="#00e676" />
            <rect x="44" y="56" width="8" height="12" fill="#334155" />
          </g>

          {/* Blinking Server Rack (bottom far right) */}
          <g transform="translate(1030, 314)">
            <rect x="0" y="0" width="82" height="168" fill="#0c111c" stroke="#2b3752" strokeWidth="2" />
            {[0, 1, 2, 3, 4, 5].map((bay) => (
              <g key={bay} transform={`translate(8, ${10 + bay * 26})`}>
                <rect x="0" y="0" width="66" height="20" fill="#131b2b" stroke="#23304a" strokeWidth="1" />
                <rect x="6" y="7" width="5" height="5" fill={bay % 2 === 0 ? "#00e676" : "#38f8ff"} />
                <rect x="15" y="7" width="5" height="5" fill={bay === 2 ? "#ff2e93" : "#00e676"} />
                <rect x="30" y="8" width="28" height="3" fill="#2a3957" />
              </g>
            ))}
          </g>

          {/* Dynamic CRAWLER Neon Investigation Web (pink / cyan / orange arcs & nodes) */}
          {webActive && token && (
            <g className="crawler-web-overlay" data-testid="crawler-web">
              {[
                { x1: 420, y1: 12, x2: 515, y2: 96, color: "#ff2e93" },
                { x1: 515, y1: 96, x2: 310, y2: 268, color: "#38f8ff" },
                { x1: 515, y1: 96, x2: 510, y2: 368, color: "#ffab00" },
                { x1: 515, y1: 96, x2: 710, y2: 268, color: "#ff2e93" },
                { x1: 515, y1: 96, x2: 710, y2: 368, color: "#38f8ff" },
              ].map((ln, idx) => (
                <g key={idx}>
                  <line x1={ln.x1} y1={ln.y1} x2={ln.x2} y2={ln.y2} stroke={ln.color} strokeWidth="2" opacity="0.88" />
                  <circle cx={ln.x2} cy={ln.y2} r="4" fill={ln.color} />
                </g>
              ))}
              <circle cx="515" cy="96" r="6" fill="#38f8ff" stroke="#ff2e93" strokeWidth="2" />
              <g transform="translate(528, 72)">
                <rect x="0" y="0" width="176" height="22" fill="#090d16" stroke="#ffab00" strokeWidth="1.5" />
                <text x="88" y="14" textAnchor="middle" fill="#ffd740" fontSize="9" fontFamily="IBM Plex Mono, monospace">
                  {playbackStep ? playbackStep.phase : `inspecting ${token.symbol}`}
                </text>
              </g>
            </g>
          )}
        </svg>

        {/* Top-Right Candidate Medallions Overlay (interactive buttons) */}
        <div className="retro-medallions" aria-label="Discovered candidate tokens">
          {desk.candidates.map((cand: CandidateToken) => {
            const active = token?.mint === cand.mint;
            const ringColor =
              cand.security_status === "pass"
                ? "#00e676"
                : cand.security_status === "fail"
                  ? "#ff384c"
                  : "#ffab00";
            return (
              <button
                key={cand.mint}
                type="button"
                className={`retro-medallion ${active ? "active" : ""}`}
                style={{ borderColor: ringColor }}
                title={`${cand.symbol} · ${cand.security_status.toUpperCase()} · Click to inspect on wall screen`}
                onClick={() => onSelectToken(cand.mint)}
              >
                <span>{cand.symbol.replace(/^DEMO-/, "")}</span>
              </button>
            );
          })}
        </div>

        {/* Animated 2D Icon-Avatar Sprites Overlay */}
        {floorAgents.map((seat) => {
          const card = seatsByName[seat];
          const pos = resolveCoords(seat, card, playbackStep);
          const color = SEAT_COLORS[seat];
          return (
            <button
              key={seat}
              type="button"
              data-testid={`floor-sprite-${seat}`}
              className={`retro-floor-sprite ${reducedMotion ? "no-motion" : ""}`}
              style={{ left: `${pos.x}%`, top: `${pos.y}%` }}
              title={`${seat}: ${card?.metric_value || "idle"} (click to inspect)`}
              onClick={() => onSelectSeat(seat)}
            >
              {pos.bubble && (
                <span className="retro-speech-bubble" style={{ borderColor: color }}>
                  {pos.bubble}
                </span>
              )}
              <span className="retro-sprite-name" style={{ color }}>
                {seat}
              </span>
              <SeatSprite seat={seat} size={seat === "CHIEF" ? 34 : 28} orbit={!reducedMotion} />
            </button>
          );
        })}

        {/* Convenience quick-open button for GmgnEmblem badge in corner */}
        <div className="retro-venue-watermark">
          <GmgnEmblem size={18} />
          <span>GMGN PAPER FLOOR · LIVE EXECUTION OFF</span>
        </div>
      </div>
    </div>
  );
}

function WorkstationDesk({
  x,
  y,
  label,
  color,
  kind,
  onClick,
}: {
  x: number;
  y: number;
  label: string;
  color: string;
  kind: "wave" | "bars" | "grid";
  onClick: () => void;
}) {
  return (
    <g transform={`translate(${x}, ${y})`} onClick={onClick} style={{ cursor: "pointer" }}>
      {/* Desk surface */}
      <rect x="0" y="24" width="128" height="44" fill="#131b2b" stroke="#2c3a57" strokeWidth="2" />
      <rect x="4" y="28" width="120" height="6" fill="#1c273d" />
      {/* Monitor frame */}
      <rect x="22" y="0" width="84" height="34" fill="#070b12" stroke="#334363" strokeWidth="2" />
      <text x="64" y="9" textAnchor="middle" fill="#8ea2c0" fontSize="6.5" fontFamily="IBM Plex Mono, monospace">
        {label}
      </text>
      {kind === "wave" && (
        <polyline
          points="28,26 40,20 52,24 66,14 80,18 98,12"
          fill="none"
          stroke={color}
          strokeWidth="2"
        />
      )}
      {kind === "bars" &&
        [0, 1, 2, 3, 4, 5, 6].map((i) => (
          <rect
            key={i}
            x={30 + i * 10}
            y={28 - ((i * 5) % 14) - 4}
            width="6"
            height={((i * 5) % 14) + 4}
            fill={color}
          />
        ))}
      {kind === "grid" &&
        [0, 1, 2, 3].map((gx) =>
          [0, 1].map((gy) => (
            <rect
              key={`${gx}-${gy}`}
              x={32 + gx * 16}
              y={13 + gy * 8}
              width="12"
              height="6"
              fill={(gx + gy) % 2 === 0 ? color : "#1e293b"}
            />
          )),
        )}
      {/* Pixel keyboard */}
      <rect x="44" y="40" width="40" height="8" fill="#0b101a" stroke="#2a3752" strokeWidth="1" />
      {/* Swivel office chair */}
      <rect x="48" y="58" width="32" height="18" fill="#0e1320" stroke="#2d3a54" strokeWidth="2" />
    </g>
  );
}

function PixelPlant({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x}, ${y})`}>
      <rect x="8" y="22" width="16" height="14" fill="#1b2436" stroke="#334155" strokeWidth="1.5" />
      <rect x="10" y="8" width="12" height="14" fill="#15803d" />
      <rect x="4" y="12" width="24" height="8" fill="#22c55e" />
      <rect x="12" y="2" width="8" height="8" fill="#4ade80" />
    </g>
  );
}
