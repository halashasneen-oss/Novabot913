from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from run_canonical_trade_audit import _prepare_named_window
from run_corrected_reference import (
    _causal_filter_factory,
    _load_modules,
    _run_engine,
    _stage_reference_files,
    _summary,
)

CANONICAL_STRATEGY_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"
WINDOWS = (
    "apr_2026",
    "may_2026",
    "jun_jul_2026",
    "jul_aug_2026",
    "aug_sep_2026",
)
CHILD_MARKER = "REPRO_CHILD="


def _data_digest(raw: dict[str, list[tuple]]) -> str:
    digest = hashlib.sha256()
    for symbol in sorted(raw):
        digest.update(symbol.encode("utf-8"))
        digest.update(b"\0")
        for bar in raw[symbol]:
            timestamp = int(bar[0])
            values = tuple(float(value) for value in bar[1:6])
            digest.update(struct.pack(">q5d", timestamp, *values))
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _result_digest(result: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(result).encode("utf-8")).hexdigest()


def _child_run(window_name: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"novabot913-repro-{window_name}-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)
        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        run_fn, raw, window = _prepare_named_window(window_name, modules)
        causal_filter = _causal_filter_factory(h, market_ref)
        result = _run_engine(h, run_fn, raw, causal_filter)
        return {
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "window_name": window_name,
            "window": window,
            "data_sha256": _data_digest(raw),
            "result_sha256": _result_digest(result),
            "summary": _summary(result),
            "result": result,
        }


def _invoke_child(window_name: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), window_name, "--child"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stdout, end="")
        print(completed.stderr, end="", file=sys.stderr)
        raise SystemExit(f"reproducibility child failed: {window_name}")
    line = next(
        (item for item in reversed(completed.stdout.splitlines()) if item.startswith(CHILD_MARKER)),
        None,
    )
    if line is None:
        raise SystemExit(f"missing reproducibility marker: {window_name}")
    return json.loads(line[len(CHILD_MARKER) :])


def _first_difference(left: Any, right: Any, path: str = "root") -> str | None:
    if type(left) is not type(right):
        return f"{path}: type {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return f"{path}: dictionary keys differ"
        for key in left:
            difference = _first_difference(left[key], right[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path}: list length {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            difference = _first_difference(
                left_item,
                right_item,
                f"{path}[{index}]",
            )
            if difference is not None:
                return difference
        return None
    if left != right:
        return f"{path}: {left!r} != {right!r}"
    return None


def _parent_run(window_name: str) -> dict[str, Any]:
    first = _invoke_child(window_name)
    second = _invoke_child(window_name)
    checks = {
        "canonical_sha_matches": (
            first["canonical_strategy_sha"]
            == second["canonical_strategy_sha"]
            == CANONICAL_STRATEGY_SHA
        ),
        "window_matches": first["window"] == second["window"],
        "input_data_sha256_matches": first["data_sha256"] == second["data_sha256"],
        "result_sha256_matches": first["result_sha256"] == second["result_sha256"],
        "summary_matches_exactly": first["summary"] == second["summary"],
        "full_result_matches_exactly": first["result"] == second["result"],
    }
    difference = _first_difference(first["result"], second["result"])
    report = {
        "phase": "canonical_reproducibility_audit",
        "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
        "strategy_parameters_modified": False,
        "window_name": window_name,
        "window": first["window"],
        "runs": [
            {
                "data_sha256": first["data_sha256"],
                "result_sha256": first["result_sha256"],
                "summary": first["summary"],
            },
            {
                "data_sha256": second["data_sha256"],
                "result_sha256": second["result_sha256"],
                "summary": second["summary"],
            },
        ],
        "checks": checks,
        "first_difference": difference,
        "pass": all(checks.values()) and difference is None,
    }
    output = Path(f"reproducibility_audit_{window_name}.json")
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print("REPRODUCIBILITY_AUDIT=" + json.dumps(report, sort_keys=True), flush=True)
    if not report["pass"]:
        raise SystemExit("canonical reproducibility audit failed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("window", choices=WINDOWS)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()

    if args.child:
        payload = _child_run(args.window)
        print(CHILD_MARKER + _canonical_json(payload), flush=True)
        return

    _parent_run(args.window)


if __name__ == "__main__":
    main()
