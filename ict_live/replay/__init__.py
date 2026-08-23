"""Historical replay — feeds past 1-minute bars through the EXACT same live pipeline (Ingestor,
BarBuilder, LiveRunner, frozen engine/filter/exit, TradeTracker, journal, reporting). One trading
engine, two data sources (live webhooks / historical replay); no separate backtester."""
