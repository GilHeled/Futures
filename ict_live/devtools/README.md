# ict_live/devtools — dev-only integration layer

**Not part of the strategy engine.** Everything here is a developer tool. The one-way rule is
enforced by `ict_live/tests/test_devtools_isolation.py`:

> `devtools/` may import the engine; **no engine module may import `devtools/`.**

The strategy engine stays fully deterministic, causal, reproducible, and runnable with the
TradingView MCP absent.

## tvmcp — TradingView MCP bridge

Drives the **TradingView Desktop** app via the [`tradesdontlie/tradingview-mcp`](https://github.com/tradesdontlie/tradingview-mcp)
`tv` CLI (Chrome DevTools Protocol, `localhost:9222`). Visual inspection / replay / drawing /
screenshots / **data cross-checking only** — never a market-data source, never authoritative.

### Setup (local dev)
1. TradingView **Desktop** (paid) running, launched with `--remote-debugging-port=9222`
   (or `tv launch`).
2. Clone the MCP repo, `npm install`, `npm link` so `tv` is on PATH — or set
   `TV_CLI="node /path/to/repo/src/cli/index.js"` and pass `--tv-cwd`.
3. `tv health_check` should succeed.

### Phase 0 — capability probe (current stage)
Answers the four approved unknowns and classifies each capability SAFE / CONDITIONAL / UNSAFE
for **causal** fidelity work. Builds no overlays and no `visual_audit`.

```bash
TV_CLI=tv python3 -m ict_live.devtools.tvmcp.probe --symbol ES1! --timeframe 60 \
    --replay-date 2025-03-14
```

Writes `results/phase0_probe_<ts>.{json,md}`. If the MCP is unreachable it writes a
"NOT EXECUTED" report with remediation and exits 2 — it never fabricates observations.

**Causal safety rule (frozen for this layer):** any tool that exposes information beyond the
Bar Replay cursor is UNSAFE and must not be used in a causal audit; a leak that can be removed
by deterministic client-side truncation to `ts<=cursor` is CONDITIONAL. Overlays/`visual_audit`
(later phases) will only use capabilities the probe marked SAFE (or CONDITIONAL with its stated
mitigation).

Files: `client.py` (the only module that knows the MCP exists) · `probe.py` (Phase 0) ·
`results/` (probe reports). `overlay.py` / `audit.py` come in later phases, after approval.
