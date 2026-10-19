# TRED Mini App

TRED v0.3.14 for @Tr1edBot_bot. Open https://kofemolka787.github.io/Tred/ from Telegram.

[Setup and update in Russian](backend-src/CONNECT-RU.md) · [Backend source](backend-src/) · [Download 0.3.14](https://kofemolka787.github.io/Tred/tred-backend-v0.3.14.zip)

Read-only demo/real Pocket balances, Telegram-scoped connection discovery, explainable 5/10/15-minute price-action scenarios, payout filtering (prefer 92%, floor 85%), and user-confirmed live trade observations. The actual owner's real/demo balance delivery and cross-device cabinet have been confirmed. This new analysis release needs validation against their incoming candle formats.

The analyzer uses causal confirmed pivots, directional structure, BOS/displacement, proposed order-block and FVG reactions, sweep patterns and channel boundaries, with multi-timeframe agreement. It is explicit rule-based analysis: no trained neural network or external LLM, and no measured strategy win probability. OTC candle patterns do not establish exchange order flow or actual liquidity. Longer expiry is not an accuracy guarantee. A missing history, stale feed or absent setup returns a reason instead of a direction.

The connector observes incoming data from the open terminal; it does not subscribe to all instruments or control the broker's charts. Coverage separates catalog, payout/expiry eligibility, fresh quotes and sufficient closed bars. Read-only delivery is separate from brokerage order execution, which is not implemented.

After marking an independently opened trade, the same signed user can follow received quotes on another device and report win/loss/draw after the timer. Observed entry prices, manual results, broker-reported trades and simulation are explicitly labelled. User reports never change broker balances or broker-verified statistics/goal profit. Signed API checks, persistent isolation, no duplicate starts/results, and freshness/entry-window guards apply.

Functional fixtures cover a real analysis engine, actual signed temporary backend, and two independent browser clients; these are not a strategy profitability backtest or native broker certification. No bot tokens, passwords, sessions, private databases or owner-specific IDs are published. See the Russian guide for setup, remaining data limits and exact checks.
