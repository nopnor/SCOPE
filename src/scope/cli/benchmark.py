from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and summarize SCOPE benchmarks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    materialize_gen_arena = subparsers.add_parser(
        "materialize-gen-arena",
        help="Materialize Gen-Arena prompt JSONL into prompt-only SCOPE generation cases.",
    )
    materialize_gen_arena.add_argument(
        "--prompt-jsonl",
        default=r"/path/to/Gen-Arena/4-entertainment/prompt.jsonl",
        help="Gen-Arena prompt JSONL with id/prompt.",
    )
    materialize_gen_arena.add_argument("--output-root", default="benchmark/materialized/gen_arena_4_entertainment")
    materialize_gen_arena.add_argument("--dataset", default="Gen-Arena/4-entertainment")
    materialize_gen_arena.add_argument("--type", default="4-entertainment")
    materialize_gen_arena.add_argument("--limit", type=int, default=None)

    materialize_gen_arena_eval = subparsers.add_parser(
        "materialize-gen-arena-eval",
        help="Materialize Gen-Arena eval.jsonl files into runtime and evaluation packets.",
    )
    materialize_gen_arena_eval.add_argument(
        "--dataset-root",
        default=r"/path/to/Gen-Arena",
        help="Root containing category folders with eval.jsonl files.",
    )
    materialize_gen_arena_eval.add_argument("--output-root", default="benchmark/materialized/gen_arena")
    materialize_gen_arena_eval.add_argument(
        "--categories",
        default="",
        help="Comma-separated category folders to include. Defaults to every folder with eval.jsonl.",
    )
    materialize_gen_arena_eval.add_argument("--limit", type=int, default=None, help="Maximum total cases.")
    materialize_gen_arena_eval.add_argument(
        "--limit-per-category",
        type=int,
        default=None,
        help="Maximum cases to keep from each category.",
    )

    summarize = subparsers.add_parser("summarize", help="Summarize benchmark metrics into JSON and CSV.")
    summarize.add_argument("--run-root", required=True)
    summarize.add_argument("--output-json", default=None)
    summarize.add_argument("--output-csv", default=None)

    run_codex = subparsers.add_parser(
        "run-codex",
        help="Dispatch materialized benchmark cases to independent Codex main-skill runs.",
    )
    run_codex.add_argument("--index", required=True, help="Path to cases.index.json from materialize-gen-arena-eval.")
    run_codex.add_argument("--run-root", required=True, help="Output root for per-case full workflow runs.")
    run_codex.add_argument("--concurrency", type=int, default=1, help="Maximum number of concurrent Codex cases.")
    run_codex.add_argument("--max-cases", type=int, default=None, help="Optional maximum number of cases to dispatch.")
    run_codex.add_argument("--repo-root", default=".", help="SCOPE repository root passed to codex exec --cd.")
    run_codex.add_argument("--codex-bin", default="codex", help="Codex executable.")
    run_codex.add_argument("--model", default="", help="Optional Codex model name.")
    run_codex.add_argument("--sandbox", default="danger-full-access", help="Codex exec sandbox mode.")
    run_codex.add_argument("--timeout-seconds", type=int, default=0, help="Per-case timeout; 0 means no timeout.")
    run_codex.add_argument(
        "--variant",
        choices=["full", "no_retrieval_reasoning", "no_repair"],
        default="full",
        help="Workflow variant for ablations. Default runs the full SCOPE workflow.",
    )
    run_codex.add_argument(
        "--codex-arg",
        action="append",
        default=[],
        help="Extra argument passed to codex exec. Repeat for multiple arguments.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "materialize-gen-arena":
        return _materialize_gen_arena(args)
    if args.command == "materialize-gen-arena-eval":
        return _materialize_gen_arena_eval(args)
    if args.command == "run-codex":
        return _run_codex_batch(args)
    return _summarize(args)


def _materialize_gen_arena(args: argparse.Namespace) -> int:
    source_path = Path(args.prompt_jsonl).resolve()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid JSON object at {source_path}:{line_no}")
        case_id = str(raw.get("id") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not case_id or not prompt:
            raise ValueError(f"Missing id/prompt at {source_path}:{line_no}")
        case_dir = output_root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        input_path = case_dir / "benchmark_input.json"
        input_path.write_text(
            json.dumps(
                {
                    "source": "materialized_gen_arena_input",
                    "case_id": case_id,
                    "prompt": prompt,
                    "input_images": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        index_rows.append(
            {
                "case_id": case_id,
                "input_json": str(input_path),
                "prompt": prompt,
                "dataset": str(args.dataset).strip(),
                "type": str(args.type).strip(),
                "id": _gen_arena_numeric_id(case_id),
            }
        )
        if args.limit is not None and len(index_rows) >= args.limit:
            break

    index_path = output_root / "cases.index.json"
    index_path.write_text(json.dumps(index_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Materialized {len(index_rows)} Gen-Arena generation cases: {index_path}")
    return 0


def _materialize_gen_arena_eval(args: argparse.Namespace) -> int:
    dataset_root = Path(args.dataset_root).resolve()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    selected_categories = {item.strip() for item in str(args.categories or "").split(",") if item.strip()}
    category_dirs = [
        path
        for path in sorted(dataset_root.iterdir())
        if path.is_dir() and (path / "eval.jsonl").is_file() and (not selected_categories or path.name in selected_categories)
    ]
    if not category_dirs:
        raise FileNotFoundError(f"No eval.jsonl files found under {dataset_root}")

    index_rows: list[dict[str, Any]] = []
    for category_dir in category_dirs:
        kept_in_category = 0
        eval_jsonl = category_dir / "eval.jsonl"
        for line_no, raw_line in enumerate(eval_jsonl.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            raw = json.loads(line)
            case_id = str(raw.get("id") or "").strip()
            prompt = str(raw.get("prompt") or "").strip()
            if not case_id or not prompt:
                raise ValueError(f"Missing id/prompt at {eval_jsonl}:{line_no}")
            case_dir = output_root / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            input_path = case_dir / "benchmark_input.json"
            eval_path = case_dir / "benchmark_eval.json"
            input_path.write_text(
                json.dumps(
                    {
                        "source": "gen_arena_eval_input",
                        "case_id": case_id,
                        "prompt": prompt,
                        "input_images": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            eval_path.write_text(
                json.dumps(
                    {
                        "source": "gen_arena_eval_spec",
                        "case_id": case_id,
                        "dataset": f"{dataset_root.name}/{category_dir.name}",
                        "type": category_dir.name,
                        "id": _gen_arena_numeric_id(case_id),
                        "reference_images": _resolve_gen_arena_reference_images(
                            category_dir=category_dir,
                            reference_images=raw.get("reference_images"),
                        ),
                        "entities": raw.get("entities", []),
                        "constraints": raw.get("constraints", []),
                        "eval_jsonl": str(eval_jsonl),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            index_rows.append(
                {
                    "case_id": case_id,
                    "input_json": str(input_path),
                    "eval_json": str(eval_path),
                    "prompt": prompt,
                    "dataset": f"{dataset_root.name}/{category_dir.name}",
                    "type": category_dir.name,
                    "id": _gen_arena_numeric_id(case_id),
                }
            )
            kept_in_category += 1
            if args.limit_per_category is not None and kept_in_category >= args.limit_per_category:
                break
            if args.limit is not None and len(index_rows) >= args.limit:
                break
        if args.limit is not None and len(index_rows) >= args.limit:
            break

    index_path = output_root / "cases.index.json"
    index_path.write_text(json.dumps(index_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Materialized {len(index_rows)} Gen-Arena eval cases: {index_path}")
    return 0


def _resolve_gen_arena_reference_images(*, category_dir: Path, reference_images: object) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for item in reference_images if isinstance(reference_images, list) else []:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        path_value = str(record.get("path", "")).strip()
        if path_value:
            candidate = Path(path_value)
            if not candidate.is_absolute():
                candidate = category_dir / path_value
            record["path"] = str(candidate)
        resolved.append(record)
    return resolved


def _gen_arena_numeric_id(case_id: str) -> int | str:
    suffix = str(case_id).rsplit("_", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return case_id


def _materialize_manifest_cases(*, manifest_path: Path, output_root: Path) -> Path:
    cases = read_manifest(manifest_path)
    output_root.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id", "")).strip()
        if not case_id:
            raise ValueError("manifest case is missing case_id")
        case_dir = output_root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        input_path = case_dir / "benchmark_input.json"
        eval_path = case_dir / "benchmark_eval.json"
        input_path.write_text(
            json.dumps(_runtime_benchmark_input(case), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        eval_path.write_text(
            json.dumps(_benchmark_eval_packet(case), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        index_rows.append(
            {
                "case_id": case_id,
                "input_json": str(input_path),
                "eval_json": str(eval_path),
                "prompt": str(case.get("prompt", "")).strip(),
                "dataset": str(case.get("dataset", "")).strip(),
                "type": str(case.get("type", "")).strip(),
            }
        )
    index_path = output_root / "cases.index.json"
    index_path.write_text(json.dumps(index_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_path


def read_manifest(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("manifest rows must be JSON objects")
                cases.append(row)
    return cases


def _run_codex_batch(args: argparse.Namespace) -> int:
    index_path = Path(args.index)
    repo_root = Path(args.repo_root).resolve()
    run_root = Path(args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    rows = _read_case_index(index_path)
    if args.max_cases is not None:
        rows = rows[: args.max_cases]
    if not rows:
        raise ValueError("case index contains no cases to dispatch")

    config = {
        "source": "scope-benchmark run-codex",
        "index": str(index_path),
        "run_root": str(run_root),
        "repo_root": str(repo_root),
        "concurrency": args.concurrency,
        "max_cases": args.max_cases,
        "codex_bin": args.codex_bin,
        "model": args.model,
        "sandbox": args.sandbox,
        "variant": args.variant,
        "timeout_seconds": args.timeout_seconds,
        "case_count": len(rows),
        "started_at": _utc_now(),
    }
    (run_root / "dispatch_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    max_workers = max(1, int(args.concurrency or 1))
    results: list[dict[str, Any]] = []
    case_order = {str(row["case_id"]): index for index, row in enumerate(rows)}
    if max_workers == 1:
        for row in rows:
            result = _dispatch_codex_case(row=row, index_path=index_path, args=args, repo_root=repo_root, run_root=run_root)
            results.append(result)
            _print_dispatch_result(result)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _dispatch_codex_case,
                    row=row,
                    index_path=index_path,
                    args=args,
                    repo_root=repo_root,
                    run_root=run_root,
                )
                for row in rows
            ]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                _print_dispatch_result(result)

    results.sort(key=lambda item: case_order.get(str(item.get("case_id", "")), len(case_order)))
    summary_path = run_root / "dispatch_summary.json"
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_metrics_summary(run_root=run_root)
    failures = [item for item in results if item.get("returncode") != 0 or not item.get("metrics_exists")]
    print(f"Dispatched cases: {len(results)}")
    print(f"Dispatch summary: {summary_path}")
    return 1 if failures else 0


def _read_case_index(index_path: Path) -> list[dict[str, Any]]:
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("cases.index.json must contain a JSON list")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("cases.index.json rows must be JSON objects")
        case_id = str(row.get("case_id", "")).strip()
        input_json = str(row.get("input_json", "")).strip()
        if not case_id or not input_json:
            raise ValueError("case index rows require case_id and input_json")
        normalized.append(row)
    return normalized


def _dispatch_codex_case(
    *,
    row: dict[str, Any],
    index_path: Path,
    args: argparse.Namespace,
    repo_root: Path,
    run_root: Path,
) -> dict[str, Any]:
    case_id = str(row["case_id"]).strip()
    run_dir = run_root / case_id
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "codex_task.prompt.md"
    stdout_path = run_dir / "codex_stdout.log"
    stderr_path = run_dir / "codex_stderr.log"
    last_message_path = run_dir / "codex_last_message.md"
    metrics_path = run_dir / "metrics.json"
    input_path = _resolve_index_file(row["input_json"], index_path=index_path)
    command: list[str] = []
    started_at = _utc_now()
    start_time = time.monotonic()
    returncode = 1
    error = ""
    try:
        benchmark_input = json.loads(input_path.read_text(encoding="utf-8"))
        prompt = _build_codex_prompt(
            case_id=case_id,
            benchmark_input=benchmark_input,
            benchmark_input_path=input_path,
            run_dir=run_dir,
            repo_root=repo_root,
            variant=str(getattr(args, "variant", "full") or "full"),
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        resolved_codex_bin = _resolve_codex_bin(str(args.codex_bin))
        command = _build_codex_command(
            codex_bin=resolved_codex_bin,
            repo_root=repo_root,
            model=str(args.model or ""),
            sandbox=str(args.sandbox or ""),
            last_message_path=last_message_path,
            extra_args=[str(item) for item in args.codex_arg],
        )

        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            cwd=str(repo_root),
            capture_output=True,
            timeout=int(args.timeout_seconds) if int(args.timeout_seconds or 0) > 0 else None,
        )
        returncode = int(completed.returncode)
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        error = f"codex task timed out after {args.timeout_seconds} seconds"
        stdout_path.write_text(_subprocess_text(exc.stdout), encoding="utf-8")
        stderr_path.write_text(_subprocess_text(exc.stderr) + f"\n{error}\n", encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive wrapper for local CLI failures.
        error = str(exc)
        stderr_path.write_text(error, encoding="utf-8")

    ended_at = _utc_now()
    duration = round(time.monotonic() - start_time, 3)
    metrics_path = run_dir / "metrics.json"
    result = {
        "case_id": case_id,
        "returncode": returncode,
        "error": error,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration,
        "command": command,
        "run_dir": str(run_dir),
        "benchmark_input_json": str(input_path),
        "prompt_path": str(prompt_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "last_message_path": str(last_message_path),
        "metrics_path": str(metrics_path),
        "metrics_exists": metrics_path.exists(),
    }
    (run_dir / "codex_task.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _resolve_index_file(path_value: str, *, index_path: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate
    index_candidate = index_path.parent / path
    if index_candidate.exists():
        return index_candidate
    return cwd_candidate

def _resolve_codex_bin(codex_bin_arg: str) -> str:
    explicit = str(codex_bin_arg or "").strip()
    if explicit and explicit.lower() != "codex":
        return explicit

    resolved = (
        os.environ.get("CODEX_BIN")
        or shutil.which("codex.cmd")
        or shutil.which("codex")
    )
    if not resolved:
        raise FileNotFoundError(
            "Unable to locate Codex CLI. Set CODEX_BIN, pass --codex-bin, or add codex/codex.cmd to PATH."
        )
    return resolved


def _build_codex_command(
    *,
    codex_bin: str,
    repo_root: Path,
    model: str,
    sandbox: str,
    last_message_path: Path,
    extra_args: list[str],
) -> list[str]:
    command = [
        codex_bin,
        "exec",
        "--cd",
        str(repo_root),
        "--skip-git-repo-check",
        "--output-last-message",
        str(last_message_path),
    ]
    if sandbox:
        command.extend(["--sandbox", sandbox])
    if model:
        command.extend(["--model", model])
    command.extend(extra_args)
    command.append("-")
    return command


def _build_codex_prompt(
    *,
    case_id: str,
    benchmark_input: dict[str, Any],
    benchmark_input_path: Path,
    run_dir: Path,
    repo_root: Path,
    variant: str = "full",
) -> str:
    prompt = str(benchmark_input.get("prompt", "")).strip()
    input_images = [str(item).strip() for item in benchmark_input.get("input_images", []) if str(item).strip()]
    variant_instructions = _variant_instructions(variant)
    return f"""You are running one SCOPE benchmark case with the full Codex-owned workflow.

Repository: {repo_root}
Top-level skill: .codex/skills/scope-agentic-generation/SKILL.md
Case id: {case_id}
Run directory: {run_dir}
Benchmark runtime input: {benchmark_input_path}
Workflow variant: {variant}
Prompt:
{prompt}

Runtime-visible input images:
{json.dumps(input_images, ensure_ascii=False, indent=2)}

Hard rules:
- Do not edit source files, documentation, configs, tests, benchmark manifests, or skill files.
- Do not call scope-benchmark to execute this case.
- Use only the runtime input above for generation workflow state.
- Do not open or use the sibling benchmark_eval.json.
- Do not use evaluation labels, evaluator reference images, scoring rubrics, or external test annotations while generating, verifying, retrieving, reasoning, or repairing.
- Write all case artifacts under the run directory.
- Follow the ownership rule in the main skill: the parent writes top-level artifacts including verification.input.json; scope-retriever, scope-reasoner, and scope-repairer own their own branch artifacts and stage calls.
- For I2I worksheet or multiple-choice input images, preserve the input image and make the smallest visible edit needed to mark the selected option; do not rewrite the full worksheet or expand an option into a verbose numeric answer unless the prompt explicitly asks for that value.

Variant-specific rules:
{variant_instructions}

Required execution:
1. Write {run_dir}\\decomposition.input.json from the prompt and runtime-visible input only.
   - decomposition.input.json must include keys: entities, constraints, unknowns.
   - entities/constraints/unknowns cannot all be empty.
   - If uncertain, put unresolved items into unknowns instead of leaving all three empty.
   - Before running decompose, self-check: len(entities)+len(constraints)+len(unknowns) > 0.
   - If this check fails, stop and fix decomposition.input.json; do not call decompose.
2. Load the exact prompt from benchmark_input.json and pass it safely to decompose:
   python -m scope.cli.stage --stage decompose --prompt "<prompt from benchmark_input.json>" --decomposition-json {run_dir}\\decomposition.input.json --benchmark-input-json {benchmark_input_path} --state {run_dir}\\state.json --output-dir {run_dir}
3. Continue the scope-agentic-generation workflow according to the variant-specific rules above:
   decompose -> conditional retrieve/reason -> synthesize -> generate -> Codex-authored verify -> repair or reroute on new unknowns -> finalize -> eval.
   Branches disabled by the variant-specific rules must not be invoked even if the normal workflow would trigger them.
4. If an external-reference branch is needed and enabled by the current variant, follow .codex/skills/scope-retriever/SKILL.md.
5. If a semantic-reasoning branch is needed and enabled by the current variant, follow .codex/skills/scope-reasoner/SKILL.md.
6. If a repair branch is needed and enabled by the current variant, follow .codex/skills/scope-repairer/SKILL.md.
7. Run:
   python -m scope.cli.evaluate --run-dir {run_dir}

Completion requirements:
- {run_dir}\\state.json exists.
- {run_dir}\\final_prompt.txt exists unless the run failed before finalize.
- {run_dir}\\metrics.json exists.
- Final response should briefly report case_id, run_dir, generated image path if available, and metrics path.
"""


def _variant_instructions(variant: str) -> str:
    normalized = str(variant or "full").strip().lower()
    if normalized == "no_retrieval_reasoning":
        return """- Run the ablation variant without retrieval and reasoning skills.
- After decompose, do not invoke scope-retriever or scope-reasoner at any point.
- Do not run python -m scope.cli.search.
- Do not create retrieval.query_plan.json, search_results.json, retrieval.input.json, retrieval.json, reasoning.input.json, or reasoning.json.
- Synthesize using only the original prompt, runtime-visible input/reference images, entities, and constraints from decomposition.
- During verification, do not emit new_unknowns for information gaps; record unresolved or missing information as fail or uncertain review_results.
- Repair is still allowed when verification finds failed or uncertain items.
- Continue until all review verdicts pass or max_iterations is reached."""
    if normalized == "no_repair":
        return """- Run the ablation variant without the repair skill.
- Retrieval and reasoning are allowed whenever open unknowns require them.
- After verify, if new_unknowns are found, resolve them with retrieval/reasoning and continue through synthesize -> generate -> verify.
- If verification has fail or uncertain items and no new_unknowns, do not invoke scope-repairer.
- Do not create repair.input.json or repair_decision.json.
- Do not run the repair stage.
- Finalize directly after that verification."""
    return """- Run the full SCOPE workflow.
- Use conditional retrieval, reasoning, verification-guided repair, and iterative generation according to the main skill."""


def _print_dispatch_result(result: dict[str, Any]) -> None:
    status = "ok" if result.get("returncode") == 0 and result.get("metrics_exists") else "failed"
    print(
        f"{result.get('case_id')}: {status} "
        f"returncode={result.get('returncode')} metrics={result.get('metrics_exists')} "
        f"run_dir={result.get('run_dir')}"
    )


def _write_metrics_summary(*, run_root: Path) -> None:
    rows: list[dict[str, Any]] = []
    for metrics_path in sorted(run_root.glob("*/metrics.json")):
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        rows.append({"case_id": metrics_path.parent.name, **metrics})
    (run_root / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    output_csv = run_root / "summary.csv"
    if rows:
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            fieldnames = sorted({key for row in rows for key in row.keys()})
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        output_csv.write_text("", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subprocess_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _runtime_benchmark_input(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "materialized_benchmark_input",
        "case_id": str(case.get("case_id", "")).strip(),
        "prompt": str(case.get("prompt", "")).strip(),
        "input_images": [str(item).strip() for item in case.get("input_images", []) if str(item).strip()],
    }


def _benchmark_eval_packet(case: dict[str, Any]) -> dict[str, Any]:
    packet = {
        "source": "materialized_benchmark_eval",
        "case_id": str(case.get("case_id", "")).strip(),
        "dataset": str(case.get("dataset", "")).strip(),
        "type": str(case.get("type", "")).strip(),
        "id": case.get("id"),
        "generation_type": str(case.get("generation_type", "")).strip(),
        "reference_images": [str(item).strip() for item in case.get("reference_images", []) if str(item).strip()],
        "checklist": [str(item) for item in case.get("checklist", [])],
        "evaluation_reference_images": [
            str(item).strip() for item in case.get("evaluation_reference_images", []) if str(item).strip()
        ],
        "world_knowledge_text": str(case.get("world_knowledge_text", "")).strip(),
        "world_knowledge_urls": [
            str(item).strip() for item in case.get("world_knowledge_urls", []) if str(item).strip()
        ],
    }
    return packet


def _summarize(args: argparse.Namespace) -> int:
    run_root = Path(args.run_root)
    rows: list[dict[str, Any]] = []
    for metrics_path in sorted(run_root.glob("*/metrics.json")):
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        rows.append({"case_id": metrics_path.parent.name, **metrics})

    output_json = Path(args.output_json) if args.output_json else run_root / "summary.json"
    output_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    output_csv = Path(args.output_csv) if args.output_csv else run_root / "summary.csv"
    if rows:
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            fieldnames = sorted({key for row in rows for key in row.keys()})
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        output_csv.write_text("", encoding="utf-8")

    print(f"Cases: {len(rows)}")
    print(f"Summary JSON: {output_json}")
    print(f"Summary CSV: {output_csv}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
