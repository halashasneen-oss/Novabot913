from __future__ import annotations

from novabot913.post_causal_experiments import long_only_market_filter


def test_long_only_gate_preserves_approved_long() -> None:
    result = {"pass": True, "reason": None}

    def base(*_args: object) -> dict[str, object]:
        return result

    wrapped = long_only_market_filter(base)
    observed = wrapped("AAAUSDT", 1, 1, [], {})

    assert observed is result
    assert observed["pass"] is True


def test_long_only_gate_rejects_approved_short_without_mutating_base_result() -> None:
    result = {"pass": True, "reason": None, "checks": {"taker": True}}

    def base(*_args: object) -> dict[str, object]:
        return result

    wrapped = long_only_market_filter(base)
    observed = wrapped("AAAUSDT", 1, -1, [], {})

    assert observed is not result
    assert observed["pass"] is False
    assert observed["reason"] == "research_direction_gate"
    assert observed["research_gate"] == "LONG_ONLY"
    assert result == {"pass": True, "reason": None, "checks": {"taker": True}}


def test_long_only_gate_leaves_already_rejected_short_unchanged() -> None:
    result = {"pass": False, "reason": "baseline_rejection"}

    def base(*_args: object) -> dict[str, object]:
        return result

    wrapped = long_only_market_filter(base)
    observed = wrapped("AAAUSDT", 1, -1, [], {})

    assert observed is result
    assert observed["reason"] == "baseline_rejection"
