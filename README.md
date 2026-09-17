# Novabot913

Production implementation of the frozen Strategy 913 for Binance USD-M Futures.

## Status

Repository initialized. Strategy implementation is built on `build/strategy-913` and must remain causal: only information available at the actual decision time may be consumed.

The historical NovaArb repository is read-only reference material. Strategy 913 behavior is frozen except for narrowly authorized temporal corrections that remove proven lookahead.

## Canonical Strategy 913 reference

The one approved Strategy 913 causal reference is:

`158fb1c45a0cf88d549e301913f43435c337d7a1`

Canonical corrected-reference workflow run: `35240653406`

Canonical artifact: `corrected-causal-reference` (`10506375028`)

Canonical artifact digest:

`sha256:77ace4c097aecbcf3a6a550d34427ad521b0553692f4e4266eaf80474e57c814`

See `docs/STRATEGY_913_CANONICAL_REFERENCE.md` for the authoritative identity and scope. Later branch commits containing diagnostics, archived research, tests, documentation, or workflow infrastructure do not replace the canonical Strategy 913 behavior.

LONG-only research is rejected and not adopted into Strategy 913.

## Authorized temporal corrections

1. Premium 5m data may be consumed only after the represented 5-minute candle is fully complete.
2. Taker 5m observations may be consumed only after the represented 5-minute interval is fully complete.

No other strategy rule, threshold, universe, sizing, leverage, entry/exit rule, trailing rule, early-failure rule, or 15-minute follow-through rule may be changed without explicit authorization.

Live trading will remain disabled by default.
