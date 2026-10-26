# Signals package (v2.0.0)

The pipeline contract is stable and fixed:

```python
SignalEngine.evaluate(snapshot: TrendingSnapshot) -> list[Signal]
```

## What lives here now

- `base.py` — `Signal` / `SignalEngine` contract (stable since v0.1)
- `engine.py` — **rule book v1** (`RuleSignalEngine`): weighted technical
  votes + AI fusion + ATR risk geometry. Fully deterministic and explainable.

## Plugging in YOUR trading rules (Phase 2)

Two options:

1. **Tune the default votes** — every weight, threshold and gate lives in
   `config/config.yaml` under `signals:` (no code changes needed).
2. **Replace the engine** — subclass `SignalEngine`, implement `evaluate()`,
   and swap `RuleSignalEngine` in `main.py`. The snapshot already carries
   trending coins, AI verdicts (`snapshot.verdict_for(sym)`), macro context,
   and the market-data edge is available through `MarketDataHub`.

The risk engine (`khosro_ai_trader/risk/`), paper journal, backtester and
dashboard all work with ANY engine that satisfies the contract — so your
rules get sizing, circuit breakers, validation and tracking for free.
