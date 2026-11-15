"""Historical replay over recorded bars. Later bars are invisible at earlier decision times."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.money import q_usdc


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def visible_bars(bars: list[dict], as_of: datetime) -> list[dict]:
    return [bar for bar in bars if parse_ts(bar["ts"]) <= as_of]


def security_as_of(rows: list[dict], as_of: datetime) -> dict | None:
    known = [row for row in rows if parse_ts(row["ts"]) <= as_of]
    if not known:
        return None
    return sorted(known, key=lambda row: row["ts"])[-1]


def run_replay(dataset: dict, params: dict) -> dict:
    """Simulate entries from bars and point-in-time security only.

    Results stay inside the backtest document. They are not written to the paper book.
    """
    label = dataset.get("label") or "unlabeled dataset"
    fee_bps = Decimal(str(params.get("fee_bps", "30")))
    slippage_bps = Decimal(str(params.get("slippage_bps", "50")))
    notional = q_usdc(Decimal(str(params.get("notional", "20"))))
    min_liquidity = Decimal(str(params.get("min_liquidity", "50000")))
    adverse = Decimal(str(params.get("adverse_pct", "8"))) / Decimal("100")
    take = Decimal(str(params.get("take_profit_pct", "20"))) / Decimal("100")

    cash = Decimal(str(params.get("starting_cash", "1000")))
    peak = cash
    position = None
    trades = []
    equity_curve = []
    unverified = 0
    missing_exit = 0
    decisions = []
    coverage_gaps = []

    events: list[tuple[datetime, str, dict]] = []
    for token in dataset.get("tokens") or []:
        for bar in token.get("bars") or []:
            events.append((parse_ts(bar["ts"]), token["mint"], {"token": token, "bar": bar}))
    events.sort(key=lambda item: (item[0], item[1]))

    for as_of, mint, payload in events:
        token = payload["token"]
        bar = payload["bar"]
        bars = visible_bars(token.get("bars") or [], as_of)
        if bars[-1]["ts"] != bar["ts"]:
            raise RuntimeError("future bar leaked into the visible window")
        security = security_as_of(token.get("security") or [], as_of)
        price = Decimal(str(bar["close"]))
        liquidity = Decimal(str(bar.get("liquidity") or "0"))
        future_prices = [Decimal(str(item["close"])) for item in token.get("bars") or [] if parse_ts(item["ts"]) > as_of]
        decisions.append(
            {
                "ts": as_of.isoformat(),
                "mint": mint,
                "visible_closes": [row["close"] for row in bars],
                "hidden_future_count": len(future_prices),
                "security_known": security.get("classification") if security else None,
            }
        )
        if security is None:
            unverified += 1
            coverage_gaps.append({"mint": mint, "ts": as_of.isoformat(), "gap": "no_point_in_time_security"})
            mark = _mark(cash, position, price if position and position["mint"] == mint else None)
            equity_curve.append({"ts": as_of.isoformat(), "equity": format(mark, "f")})
            peak = max(peak, mark)
            continue
        if security.get("classification") != "pass":
            coverage_gaps.append({"mint": mint, "ts": as_of.isoformat(), "gap": "security_not_pass"})

        if position and position["mint"] == mint:
            change = (price - position["entry"]) / position["entry"]
            reason = None
            if change <= -adverse:
                reason = "adverse_price"
            elif change >= take:
                reason = "take_profit"
            if reason:
                if bar.get("exit_route") is False:
                    missing_exit += 1
                    position["unsellable"] = True
                else:
                    fill_price = price * (Decimal("1") - slippage_bps / Decimal("10000"))
                    gross = position["qty"] * fill_price
                    fee = q_usdc(gross * fee_bps / Decimal("10000"))
                    proceeds = q_usdc(gross - fee)
                    realized = q_usdc(proceeds - position["cost"])
                    cash = q_usdc(cash + proceeds)
                    trades.append(
                        {
                            "mint": mint,
                            "reason": reason,
                            "realized_pnl": format(realized, "f"),
                            "fee": format(fee, "f"),
                            "exit_ts": as_of.isoformat(),
                            "data_label": label,
                        }
                    )
                    position = None
        elif position is None and security.get("classification") == "pass" and liquidity >= min_liquidity:
            prior = bars[:-1]
            if prior and Decimal(str(prior[-1]["close"])) < price and bar.get("entry_route", True):
                fill_price = price * (Decimal("1") + slippage_bps / Decimal("10000"))
                fee = q_usdc(notional * fee_bps / Decimal("10000"))
                spend = q_usdc(notional + fee)
                if cash >= spend and fill_price > 0:
                    qty = notional / fill_price
                    cash = q_usdc(cash - spend)
                    position = {
                        "mint": mint,
                        "qty": qty,
                        "entry": fill_price,
                        "cost": spend,
                        "unsellable": False,
                    }
                    trades.append(
                        {
                            "mint": mint,
                            "reason": "entry",
                            "notional": format(notional, "f"),
                            "fee": format(fee, "f"),
                            "entry_ts": as_of.isoformat(),
                            "price_used": format(fill_price, "f"),
                            "data_label": label,
                        }
                    )

        mark_price = price if position and position["mint"] == mint else None
        equity = _mark(cash, position, mark_price)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak else Decimal("0")
        equity_curve.append(
            {
                "ts": as_of.isoformat(),
                "equity": format(equity, "f"),
                "drawdown": format(drawdown, "f"),
            }
        )

    point_in_time = unverified == 0 and not any(
        not (token.get("security") or []) for token in dataset.get("tokens") or []
    )
    return {
        "label": label,
        "point_in_time_security": point_in_time,
        "disclosure": (
            "Replay used only bars and security snapshots at or before each decision time. "
            + (
                "Point-in-time security was present for every decision."
                if point_in_time
                else "Some decisions had no point-in-time security and were not treated as verified."
            )
        ),
        "unverified_decisions": unverified,
        "missing_exits": missing_exit,
        "trades": trades,
        "equity_curve": equity_curve,
        "decisions": decisions,
        "ending_cash": format(cash, "f"),
        "open_position": bool(position),
        "coverage_gaps": coverage_gaps,
    }


def _mark(cash: Decimal, position: dict | None, price: Decimal | None) -> Decimal:
    if not position or price is None:
        return q_usdc(cash)
    return q_usdc(cash + position["qty"] * price)
