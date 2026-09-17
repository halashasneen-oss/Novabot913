from __future__ import annotations

import argparse
import json
from pathlib import Path

from novabot913.post_causal_analysis import build_post_causal_report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze the frozen corrected Strategy 913 reference"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("corrected_causal_reference.json"),
        help="Corrected causal reference JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("post_causal_diagnostics.json"),
        help="Diagnostics output JSON",
    )
    args = parser.parse_args()

    reference = json.loads(args.input.read_text(encoding="utf-8"))
    report = build_post_causal_report(reference)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    combined = report["combined"]["overall"]
    print(
        "POST_CAUSAL_DIAGNOSTICS="
        + json.dumps(
            {
                "trades": combined["trades"],
                "net": combined["net"],
                "win_rate_pct": combined["win_rate_pct"],
                "profit_factor": combined["profit_factor"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
