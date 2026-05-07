from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a SCOPE run directory.")
    parser.add_argument("--run-dir", required=True, help="Directory containing state.json.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_dir = Path(args.run_dir)
    state_path = run_dir / "state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"Missing state file: {state_path}")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    reviews = state.get("review_results", [])
    best_verification = state.get("best_verification", {}) if isinstance(state.get("best_verification", {}), dict) else {}
    passed = sum(1 for item in reviews if item.get("verdict") == "pass")
    total = len(reviews)
    best_passed = int(best_verification.get("pass_count", 0) or 0)
    best_total = int(best_verification.get("total", 0) or 0)
    metrics = {
        "case_success": bool(total and passed == total),
        "review_pass_rate": passed / total if total else 0.0,
        "iterations": state.get("iteration", 0),
        "repair_action": state.get("repair_action", "none"),
        "best_case_success": bool(best_total and best_passed == best_total),
        "best_review_pass_rate": best_passed / best_total if best_total else 0.0,
        "best_iteration": int(state.get("best_iteration", 0) or 0),
        "best_image_path": str(state.get("best_image_path", "") or ""),
    }
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics["case_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
