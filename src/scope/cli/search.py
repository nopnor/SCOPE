from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scope.runtime.search_backend import search_external_unknowns, search_query_plan
from scope.workflow.stages import load_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SCOPE web search for open external-reference unknowns.")
    parser.add_argument("--state", required=True, help="Path to state.json.")
    parser.add_argument("--output-json", required=True, help="Path to write search_results.json.")
    parser.add_argument("--query-plan-json", default="", help="Optional agent-written retrieval query plan JSON.")
    parser.add_argument(
        "--download-dir",
        default="",
        help="Optional directory for downloaded image search references. Defaults to a sibling directory.",
    )
    parser.add_argument(
        "--max-image-downloads-per-unknown",
        type=int,
        default=1,
        help="Base image download limit. Query-plan searches download at least one image for each image query.",
    )
    parser.add_argument(
        "--unknown-id",
        action="append",
        default=[],
        help="Optional unknown id to search. May be passed multiple times.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state = load_state(args.state)
    selected_ids = {str(item).strip() for item in args.unknown_id if str(item).strip()}
    unknowns = [
        item
        for item in state.unknowns
        if item.kind == "external_reference"
        and item.status == "open"
        and (not selected_ids or item.id in selected_ids)
    ]
    query_plans = []
    query_plan_path = ""
    if args.query_plan_json:
        query_plan_path = str(Path(args.query_plan_json))
        payload = json.loads(Path(args.query_plan_json).read_text(encoding="utf-8"))
        raw_plans = payload.get("query_plans", payload.get("plans", [])) if isinstance(payload, dict) else []
        if not isinstance(raw_plans, list):
            raise ValueError("query plan JSON must contain a query_plans list")
        query_plans = raw_plans
        records = search_query_plan(
            query_plans,
            unknowns=unknowns,
            download_dir=_download_dir(args),
            max_image_downloads_per_unknown=max(args.max_image_downloads_per_unknown, 0),
        )
    else:
        records = search_external_unknowns(
            unknowns,
            download_dir=_download_dir(args),
            max_image_downloads_per_unknown=max(args.max_image_downloads_per_unknown, 0),
        )
    output = {
        "state_path": str(Path(args.state)),
        "query_plan_path": query_plan_path,
        "searched_unknown_ids": [item.id for item in unknowns],
        "query_plans": query_plans,
        "results": records,
    }
    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    return 0


def _download_dir(args: argparse.Namespace) -> Path:
    if args.download_dir:
        return Path(args.download_dir)
    return Path(args.output_json).parent / "searched_reference_images"


if __name__ == "__main__":
    raise SystemExit(main())
