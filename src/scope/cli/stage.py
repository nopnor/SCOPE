from __future__ import annotations

import argparse
import json
from pathlib import Path

from scope.workflow.stages import (
    run_decompose_stage,
    run_generate_stage,
    run_reason_stage,
    run_repair_stage,
    run_retrieve_stage,
    run_synthesize_stage,
    run_verify_stage,
    write_final_prompt,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one SCOPE stage command. Workflow routing is defined in .codex/skills."
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=["decompose", "retrieve", "reason", "synthesize", "generate", "verify", "repair", "finalize"],
        help="Stage to run.",
    )
    parser.add_argument("--state", required=True, help="Path to state.json.")
    parser.add_argument("--output-dir", default=None, help="Run artifact directory.")
    parser.add_argument("--prompt", default="", help="Prompt, required for decompose.")
    parser.add_argument(
        "--decomposition-json",
        default=None,
        help="Input artifact for decompose.",
    )
    parser.add_argument(
        "--benchmark-input-json",
        default=None,
        help="Optional benchmark runtime input packet to bind during decompose.",
    )
    parser.add_argument(
        "--retrieval-json",
        default=None,
        help="Input artifact for retrieve.",
    )
    parser.add_argument(
        "--reasoning-json",
        default=None,
        help="Input artifact for reason.",
    )
    parser.add_argument(
        "--synthesis-json",
        default=None,
        help="Input artifact for synthesize.",
    )
    parser.add_argument(
        "--verification-json",
        default=None,
        help="Input artifact for Codex-owned verify.",
    )
    parser.add_argument(
        "--repair-json",
        default=None,
        help="Input artifact for repair.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state_path = Path(args.state)
    output_dir = Path(args.output_dir) if args.output_dir else state_path.parent

    def _load_json(path_value: str | None) -> dict | None:
        if not path_value:
            return None
        return json.loads(Path(path_value).read_text(encoding="utf-8-sig"))

    if args.stage == "decompose":
        if not args.prompt.strip():
            raise ValueError("--prompt is required for decompose")
        state = run_decompose_stage(
            prompt=args.prompt,
            state_path=state_path,
            output_dir=output_dir,
            decomposition_payload=_load_json(args.decomposition_json),
            benchmark_input=_load_json(args.benchmark_input_json),
            benchmark_input_path=args.benchmark_input_json,
        )
    elif args.stage == "retrieve":
        state = run_retrieve_stage(
            state_path=state_path,
            retrieval_payload=_load_json(args.retrieval_json),
        )
    elif args.stage == "reason":
        state = run_reason_stage(
            state_path=state_path,
            reasoning_payload=_load_json(args.reasoning_json),
        )
    elif args.stage == "synthesize":
        state = run_synthesize_stage(
            state_path=state_path,
            synthesis_payload=_load_json(args.synthesis_json),
        )
    elif args.stage == "generate":
        state = run_generate_stage(state_path=state_path, output_dir=output_dir)
    elif args.stage == "verify":
        state = run_verify_stage(
            state_path=state_path,
            verification_payload=_load_json(args.verification_json),
        )
    elif args.stage == "repair":
        state = run_repair_stage(
            state_path=state_path,
            repair_payload=_load_json(args.repair_json),
        )
    else:
        state = write_final_prompt(state_path=state_path, output_dir=output_dir)

    print(f"SCOPE stage complete: {args.stage}")
    print(f"State: {state_path.resolve()}")
    print(f"Trace: {' -> '.join(state.stage_trace)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
