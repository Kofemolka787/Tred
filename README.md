# TRED Mini App

TRED 0.3.25 for @Tr1edBot_bot. Open https://kofemolka787.github.io/Tred/ from Telegram.

[Setup and update in Russian](backend-src/CONNECT-RU.md) · [Strategy details](backend-src/STRATEGIES-RU.md) · [Backend source](backend-src/) · [Download 0.3.25](https://kofemolka787.github.io/Tred/tred-backend-v0.3.25.zip)

## Signal collection and AI review

The received catalog retains every instrument, including OTC and non-currency categories. Open instruments must meet the selected expiry and payout filters (prefer 92%, floor 85%). Five independent setup families cover Smart Money, EMA20/50 pullbacks, breakout/retests, range rebounds and false breakouts. Closed-bar calculations establish causal scenarios; LunaRoute or direct OpenAI compares them and chooses an exact symbol/direction/strategy tuple or declines. This is model-assisted analysis with deterministic validation, not model-weight training or measured win probabilities.

Version 0.3.25 pages backward when a native history reply contains fewer than 80 recent closed bars. Repeating the same latest page cannot complete a chart. Explicit older end bounds retain the selected source clock and are bounded to stored-history limits; stale data is refreshed at the current time first. Partial compatible periods such as M2/M10 are completed instead of being replaced with empty preferred periods. Catalog diagnostics now show actual available continuous bars per received period and their age, separately from decoded response counts. A local 19-pair fixture returning only 24 bars per response reaches 96 real fixture bars on both M5/M15 and a validated candidate; this is not a native broker latency measurement.

Chart review is separated from final entry validation. Full, continuous closed histories on two periods can enter AI review without simultaneous live ticks for every pair. The closed-bar reference is explicitly separate from the real quote. Incomplete pairs retain inventory and missing-data summaries. Partial collection can start review after a short grace period; a silent pair does not delay the entire catalog. Large catalogs use bounded batches and winner comparison, preserving all inventory rows and complete-chart facts.

After AI selection the collector focuses on the chosen pair. No direction or entry button is offered until a genuine quote at most 10 seconds old and the original scenario, price zone, payout, expiry and source are revalidated. Missing selected quotes get a bounded 12-second check, without another paid model call. Stable Smart Money anchors may survive a new closed bar only after full fresh validation; other setups require the original bar. Source changes, invalidated structures and departed price zones cancel entry. No forced rule-only fallback or automatic paid retries.

The UI distinguishes catalog rows, complete charts reviewed and fresh quotes. Indicator facts and causal levels are calculated from 80–160 closed bars; the model receives a compact recent OHLC sample. Periods are seconds (300=M5, 900=M15), with trade durations separately in minutes. The selected strategy, explanation and weakness remain in the manual trade journal.

## Connector and update

Update the entire ZIP: server 0.3.25, the same extension card to connector 0.3.15, and the Mini App interface. Refresh Pocket Option once after extension update and restart the new server. The existing private database, provider key, chosen model and confirmed account profiles persist. An unchanged server route needs no new pairing code; a changed temporary tunnel address must be saved once in both clients. See the Russian setup guide for exact Windows steps.

The connector recovers the worker's feed-to-tab mapping from actual live MAIN sockets after worker restart. Native incoming messages also renew collection plans and wake the queue when background page timers are delayed. Requests remain limited to read-only subfor/loadHistoryPeriod messages through the already-open native socket, paced at one per 400ms with at most two pending replies and one request per instrument. Only the selected missing quote receives extra priority; other history work retains capacity. No order, login, credential-copy or chart-switch commands are generated.

Bad candle rows are excluded separately rather than rejecting an otherwise valid history packet. At least 80 recent continuous closed candles are still required, and gaps are never filled. History and quotes from different connections are isolated. Transient delivery retries retain original timestamps and are bounded. UTC or explicit Pocket UTC+2 conversion, source validation and final quote freshness checks remain; closed prices are never relabeled as live ticks. The popup shows collection state and sanitized diagnostics.

## Local setup and privacy

SETUP-LUNAROUTE.cmd fetches the authenticated model catalog and verifies a short paid structured Responses request before saving the key. SETUP-AI.cmd supports direct OpenAI. Keys are entered visibly in the local setup console as requested by the owner and stored only in the private user directory, scoped to authorized signed Telegram users. No key belongs in Telegram, the Mini App or this repository. The LunaRoute gateway is https://gw.lunaroute.com/v1; no Node.js/CLI login is required. ChatGPT subscription billing is separate from API billing. Failed setup preserves previous settings; transport, quota and invalid response failures have sanitized distinct messages.

The bounded Responses client accepts completed validated SSE results without waiting for connection EOF. Immutable evidence references are checked against source facts; fabricated, cross-pair, duplicate or partial output cannot become an entry. Provider changes discard in-flight results. Only market evidence is sent to the model; no broker credentials, account balances or owner identifiers.

Demo and real balances are read only. The same signed Telegram user can follow received quotes on another device after independently opening a trade, then report win/loss/draw after expiry. Manual outcomes remain separate from broker balances. The computer, terminal and server must stay running for this local deployment.

## Verification and limits

Automated tests cover strategy recognition in both directions on 5/10/15-minute durations, source/time/zone guards, mobile UI, manual tracking, user isolation, worker recovery and preserved balances. Native-shaped broker fixtures exercise the actual observer, worker and HTTP backend; local model fixtures exercise the actual Responses/SSE client. Cases include 33 complete charts with no live ticks and a cold 41-pair catalog with page timers disabled and a delayed model. These are controlled fixtures, not native broker latency measurements.

The installed owner's Opera GX extension, live Pocket stream and actual LunaRoute replies still require post-installation verification. Managed Chromium prevents native unpacked-extension loading in this test environment; no policy bypass is used. No claim of profitability, trained weights, real exchange order flow for OTC, or guaranteed signals is made. Empty or invalid data and absent confirmed setups remain explicit waits rather than invented entries. No bot tokens, broker sessions, passwords, private databases or owner IDs are packaged.
