# TRED Mini App

TRED v0.3.15 for @Tr1edBot_bot. Open https://kofemolka787.github.io/Tred/ from Telegram.

[Setup and update in Russian](backend-src/CONNECT-RU.md) · [Backend source](backend-src/) · [Download 0.3.15](https://kofemolka787.github.io/Tred/tred-backend-v0.3.15.zip)

Read-only demo/real Pocket balances, Telegram-scoped connection discovery, explainable 5/10/15-minute price-action scenarios, payout filtering (prefer 92%, floor 85%), and user-confirmed live trade observations. The actual owner's real/demo balance delivery and cross-device cabinet have been confirmed. This new analysis release needs validation against their incoming candle formats.

The analyzer uses causal confirmed pivots, directional structure, BOS/displacement, proposed order-block and FVG reactions, sweep patterns and channel boundaries, with multi-timeframe agreement. It is explicit rule-based analysis: no trained neural network or external LLM, and no measured strategy win probability. OTC candle patterns do not establish exchange order flow or actual liquidity. Longer expiry is not an accuracy guarantee. A missing history, stale feed or absent setup returns a reason instead of a direction.

The connector observes incoming data from the open terminal, including Fast history and complete tick-derived M1 bars. A missing response period is recovered only from an exact index/asset match to the terminal's own history request; no login frame is read. Supported bars include M1/M2/M5/M10/M15, with complete lower-frame aggregation. Coverage shows actual frames, history-format diagnostics and specific data blockers. Catalog validity follows fresh quotes from the same open socket without restamping catalog time; disconnect invalidates it and a new socket needs its own catalog. The connector does not create broker requests, subscribe to all instruments or control the broker's charts. Read-only delivery is separate from brokerage order execution, which is not implemented.

After marking an independently opened trade, the same signed user can follow received quotes on another device and report win/loss/draw after the timer. Observed entry prices, manual results, broker-reported trades and simulation are explicitly labelled. User reports never change broker balances or broker-verified statistics/goal profit. Signed API checks, persistent isolation, no duplicate starts/results, and freshness/entry-window guards apply.

Functional fixtures cover a real analysis engine, actual signed temporary backend, and two independent browser clients; these are not a strategy profitability backtest or native broker certification. No bot tokens, passwords, sessions, private databases or owner-specific IDs are published. See the Russian guide for setup, remaining data limits and exact checks.
