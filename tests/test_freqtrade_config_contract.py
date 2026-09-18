from __future__ import annotations

import json
from pathlib import Path

from novabot913.strategy_core import CANONICAL_UNIVERSE

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "integrations" / "freqtrade" / "config.dryrun.example.json"


def _freqtrade_pair(symbol: str) -> str:
    base = symbol.removesuffix("USDT")
    return f"{base}/USDT:USDT"


def test_freqtrade_config_is_safe_dry_run_futures_contract() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    assert config["dry_run"] is True
    assert config["trading_mode"] == "futures"
    assert config["margin_mode"] == "isolated"
    assert config["stake_currency"] == "USDT"
    assert config["timeframe"] == "1m"

    exchange = config["exchange"]
    assert exchange["name"] == "binance"
    assert exchange["api_key"] == ""
    assert exchange["secret"] == ""
    assert "key" not in exchange

    expected_pairs = [_freqtrade_pair(symbol) for symbol in CANONICAL_UNIVERSE]
    assert exchange["pair_whitelist"] == expected_pairs


def test_freqtrade_config_uses_static_frozen_universe() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["pairlists"] == [{"method": "StaticPairList"}]
    assert config["max_open_trades"] == len(CANONICAL_UNIVERSE)
