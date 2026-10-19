# TRED Mini App

TRED v0.3.6 for @Tr1edBot_bot. Open https://kofemolka787.github.io/Tred/ from Telegram.

Mobile/desktop UI, Goal $100 → $1000, risk settings and a downloadable local backend. Offline UI works without a server. Personal account data requires verified Telegram identity plus an individually paired browser connector and a running HTTPS backend.

[Free local setup in Russian](backend-src/CONNECT-RU.md) · [Backend source](backend-src/) · [Download](https://kofemolka787.github.io/Tred/tred-backend-v0.3.6.zip)

The backend has per-user account snapshots, an idempotent manual-trade journal, daily simulation stops, exposure reservations and causal multi-timeframe price-action research. Real order execution, martingale and withdrawals are not implemented. Strategy accuracy is unmeasured; no trained neural network is provided.

Live Pocket Option integration and the Windows/tunnel workflow still require validation on the account owner's computer. The connector only analyzes data actually observed in its open browser; it does not scan all pairs automatically. Unknown broker formats are ignored rather than guessed. No bot tokens, passwords, account sessions, personal databases are published. See the setup guide for exact limitations.
