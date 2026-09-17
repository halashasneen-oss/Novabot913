# Novabot913

Production implementation of the frozen Strategy 913 for Binance USD-M Futures.

## Status

Repository initialized. Strategy implementation is built on `build/strategy-913` and must remain causal: only information available at the actual decision time may be consumed.

The historical NovaArb repository is read-only reference material. Strategy 913 behavior is frozen except for narrowly authorized temporal corrections that remove proven lookahead.

## Authorized temporal corrections

1. Premium 5m data may be consumed only after the represented 5-minute candle is fully complete.
2. Taker 5m observations may be consumed only after the represented 5-minute interval is fully complete.

No other strategy rule, threshold, universe, sizing, leverage, entry/exit rule, trailing rule, early-failure rule, or 15-minute follow-through rule may be changed without explicit authorization.

Live trading will remain disabled by default.
