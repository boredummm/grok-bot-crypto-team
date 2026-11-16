# Risk model

The risk engine is deterministic and does not call providers or a language model. Defaults assume a $1,000 simulated USDC book.

| Control | Default |
| --- | --- |
| Maximum new trade | $20 |
| Maximum opening position | 2% of equity |
| Concurrent positions | 3 |
| Gross exposure | 6% of equity |
| Daily loss cutoff | 3% of start-of-day equity |
| Drawdown cutoff | 10% from peak equity |
| Minimum pool age | 15 minutes |
| Minimum pool liquidity | $50,000 |
| Adverse exit | 8% from entry, using a sell quote |

Shorts, leverage, and borrowing are rejected. Approval requires fresh market data, a fresh quote, a passing security classification, healthy providers in live mode, buying power, and no duplicate open position.

Daily start equity and peak equity are rows in `portfolio_state`. They survive process restarts. A new UTC day rolls the daily baseline and does not reset the peak.

Emergency stop blocks new entries, cancels pending entry orders, and leaves open positions and cash untouched. Resume clears only the pause.

Security `unknown` is not a pass. Active freeze authority fails. Active mint authority fails unless a saved policy explicitly allows it. Missing exit route, stale data, and unparsed mint accounts block entry. These checks do not detect every rug.

Live fills require a Jupiter v2 order quote. If that quote is missing, the order is not invented. Demo fills are labeled `demo_fixture`.
