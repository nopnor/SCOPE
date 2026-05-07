from __future__ import annotations

import argparse
import base64
import csv
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from scope.runtime.http_retry import request_with_retry
from scope.runtime.judge_backend import _extract_jdcloud_text, _guess_mime_type, looks_like_supported_image
from scope.runtime.settings import get_runtime_settings


JD_CLOUD_RESPONSES_ENDPOINT = "/responses"
SUPPORTED_OFFICIAL_EVAL_PROVIDERS = {"gemini_jdcloud", "jdcloud_gemini", "jdcloud"}
PASS_VERDICT = "pass"
FAIL_VERDICT = "fail"


@dataclass(frozen=True)
class CaseBinding:
    case_id: str
    run_dir: Path
    eval_json: Path
    image_path: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Official Gen-Arena entity-gated evaluator.")
    parser.add_argument("--run-root", default="", help="Root containing one run directory per case.")
    parser.add_argument(
        "--materialized-root",
        default="benchmark/materialized/gen_arena",
        help="Root containing materialized case folders with benchmark_eval.json.",
    )
    parser.add_argument("--eval-json", default="", help="Single-case benchmark_eval.json path.")
    parser.add_argument("--image-path", default="", help="Single-case generated image path.")
    parser.add_argument("--case-ids", default="", help="Comma-separated case ids to evaluate.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum cases to evaluate.")
    parser.add_argument("--concurrency", type=int, default=1, help="Maximum concurrent verifier requests.")
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Defaults to benchmark/evaluations/gen_arena_official_<timestamp>.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = _resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bindings = _collect_case_bindings(args)
    if args.limit is not None:
        bindings = bindings[: args.limit]
    if not bindings:
        raise ValueError("No Gen-Arena cases found for official evaluation.")

    results_path = output_dir / "case_results.jsonl"
    summary_json = output_dir / "summary.json"
    summary_csv = output_dir / "summary.csv"

    rows: list[dict[str, Any]] = []
    max_workers = max(1, int(args.concurrency or 1))
    with results_path.open("w", encoding="utf-8") as handle:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(evaluate_case, binding=binding): binding
                for binding in bindings
            }
            for future in as_completed(futures):
                binding = futures[future]
                try:
                    row = future.result()
                except Exception as exc:  # Keep batch runs inspectable when one case fails.
                    row = {
                        "case_id": binding.case_id,
                        "run_dir": str(binding.run_dir),
                        "eval_json": str(binding.eval_json),
                        "image_path": str(binding.image_path),
                        "error": str(exc),
                        "egip_pass": False,
                    }
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                status = "PASS" if row.get("egip_pass") else "FAIL"
                if row.get("error"):
                    status = "ERROR"
                print(f"[{status}] {binding.case_id}")

    rows.sort(key=lambda item: str(item.get("case_id", "")))
    summary = summarize_results(rows)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_summary_csv(summary_csv, summary)
    print(json.dumps({"results": str(results_path), "summary": str(summary_json), "csv": str(summary_csv)}, indent=2))
    return 0


def evaluate_case(*, binding: CaseBinding) -> dict[str, Any]:
    eval_spec = json.loads(binding.eval_json.read_text(encoding="utf-8"))
    raw_payload = _call_official_verifier(eval_spec=eval_spec, image_path=binding.image_path)
    verifier_backend = "api"
    scored = score_gen_arena_case(eval_spec=eval_spec, verifier_payload=raw_payload)
    return {
        "case_id": binding.case_id,
        "dataset": eval_spec.get("dataset", ""),
        "type": eval_spec.get("type", ""),
        "run_dir": str(binding.run_dir),
        "eval_json": str(binding.eval_json),
        "image_path": str(binding.image_path),
        "verifier_backend": verifier_backend,
        **scored,
        "error": "",
    }


def score_gen_arena_case(*, eval_spec: dict[str, Any], verifier_payload: dict[str, Any]) -> dict[str, Any]:
    entities = list(eval_spec.get("entities") or [])
    constraints = list(eval_spec.get("constraints") or [])
    entity_by_id = {str(item.get("id")): item for item in entities}
    constraint_by_id = {str(item.get("id")): item for item in constraints}

    entity_results = _normalize_item_results(verifier_payload.get("entities"), entity_by_id.keys())
    constraint_results = _normalize_item_results(verifier_payload.get("constraints"), constraint_by_id.keys())

    scored_entities: list[dict[str, Any]] = []
    entity_pass_by_id: dict[str, bool] = {}
    for entity in entities:
        entity_id = str(entity.get("id"))
        result = entity_results[entity_id]
        passed = result["verdict"] == PASS_VERDICT
        entity_pass_by_id[entity_id] = passed
        scored_entities.append(
            {
                "id": entity_id,
                "name": entity.get("name", ""),
                "verdict": result["verdict"],
                "passed": passed,
                "reason": result.get("reason", ""),
                "evidence": result.get("evidence", ""),
            }
        )

    scored_constraints: list[dict[str, Any]] = []
    direct_constraint_passes = 0
    gated_constraint_passes = 0
    constraint_type_counts: dict[str, dict[str, int]] = {}
    for constraint in constraints:
        constraint_id = str(constraint.get("id"))
        result = constraint_results[constraint_id]
        direct_passed = result["verdict"] == PASS_VERDICT
        depends_on = [str(item) for item in constraint.get("depends_on") or []]
        prerequisite_passed = all(entity_pass_by_id.get(entity_id, False) for entity_id in depends_on)
        gated_passed = direct_passed and prerequisite_passed
        direct_constraint_passes += int(direct_passed)
        gated_constraint_passes += int(gated_passed)
        constraint_type = str(constraint.get("type") or "unknown")
        bucket = constraint_type_counts.setdefault(
            constraint_type,
            {"total": 0, "direct_pass": 0, "gated_pass": 0},
        )
        bucket["total"] += 1
        bucket["direct_pass"] += int(direct_passed)
        bucket["gated_pass"] += int(gated_passed)
        scored_constraints.append(
            {
                "id": constraint_id,
                "type": constraint_type,
                "text": constraint.get("text", ""),
                "depends_on": depends_on,
                "direct_verdict": result["verdict"],
                "direct_passed": direct_passed,
                "prerequisite_passed": prerequisite_passed,
                "gated_passed": gated_passed,
                "reason": result.get("reason", ""),
                "evidence": result.get("evidence", ""),
            }
        )

    entity_passes = sum(1 for item in scored_entities if item["passed"])
    egip_pass = entity_passes == len(entities) and gated_constraint_passes == len(constraints)
    return {
        "egip_pass": bool(egip_pass),
        "entity_pass_count": entity_passes,
        "entity_total": len(entities),
        "constraint_direct_pass_count": direct_constraint_passes,
        "constraint_gated_pass_count": gated_constraint_passes,
        "constraint_total": len(constraints),
        "entity_pass_rate": _safe_div(entity_passes, len(entities)),
        "constraint_direct_pass_rate": _safe_div(direct_constraint_passes, len(constraints)),
        "constraint_gated_pass_rate": _safe_div(gated_constraint_passes, len(constraints)),
        "constraint_type_counts": constraint_type_counts,
        "entities": scored_entities,
        "constraints": scored_constraints,
    }


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    errors = [row for row in rows if row.get("error")]
    evaluated = [row for row in rows if not row.get("error")]
    egip_passes = sum(1 for row in rows if row.get("egip_pass") is True)
    summary: dict[str, Any] = {
        "cases": total,
        "evaluated_cases": len(evaluated),
        "error_cases": len(errors),
        "egip": _safe_div(egip_passes, total),
        "egip_pass_count": egip_passes,
        "entity_pass_rate": _aggregate_rate(evaluated, "entity_pass_count", "entity_total"),
        "constraint_direct_pass_rate": _aggregate_rate(
            evaluated,
            "constraint_direct_pass_count",
            "constraint_total",
        ),
        "constraint_gated_pass_rate": _aggregate_rate(
            evaluated,
            "constraint_gated_pass_count",
            "constraint_total",
        ),
        "by_category": {},
        "by_constraint_type": {},
    }

    category_rows: dict[str, list[dict[str, Any]]] = {}
    type_counts: dict[str, dict[str, int]] = {}
    for row in evaluated:
        category_rows.setdefault(str(row.get("type") or "unknown"), []).append(row)
        for constraint_type, counts in (row.get("constraint_type_counts") or {}).items():
            bucket = type_counts.setdefault(constraint_type, {"total": 0, "direct_pass": 0, "gated_pass": 0})
            bucket["total"] += int(counts.get("total", 0))
            bucket["direct_pass"] += int(counts.get("direct_pass", 0))
            bucket["gated_pass"] += int(counts.get("gated_pass", 0))

    for category, items in sorted(category_rows.items()):
        summary["by_category"][category] = {
            "cases": len(items),
            "egip": _safe_div(sum(1 for row in items if row.get("egip_pass") is True), len(items)),
            "entity_pass_rate": _aggregate_rate(items, "entity_pass_count", "entity_total"),
            "constraint_direct_pass_rate": _aggregate_rate(items, "constraint_direct_pass_count", "constraint_total"),
            "constraint_gated_pass_rate": _aggregate_rate(items, "constraint_gated_pass_count", "constraint_total"),
        }

    for constraint_type, counts in sorted(type_counts.items()):
        summary["by_constraint_type"][constraint_type] = {
            "total": counts["total"],
            "direct_pass_rate": _safe_div(counts["direct_pass"], counts["total"]),
            "gated_pass_rate": _safe_div(counts["gated_pass"], counts["total"]),
        }
    return summary


def _call_official_verifier(*, eval_spec: dict[str, Any], image_path: Path) -> dict[str, Any]:
    settings = get_runtime_settings()
    provider = (settings.judge.provider or "").lower()
    if provider not in SUPPORTED_OFFICIAL_EVAL_PROVIDERS:
        raise RuntimeError(
            f"Unsupported official evaluator provider '{settings.judge.provider}'. "
            f"Supported providers: {sorted(SUPPORTED_OFFICIAL_EVAL_PROVIDERS)}"
        )
    if not settings.judge.configured:
        raise RuntimeError("Official evaluator requires configured SCOPE_JUDGE_* API settings.")

    image_bytes = image_path.read_bytes()
    if not looks_like_supported_image(image_bytes):
        raise RuntimeError(f"Generated image is not a supported raster image: {image_path}")
    parts: list[dict[str, Any]] = [{"text": "Generated image to evaluate:"}, _inline_image_part(image_bytes)]

    reference_images = []
    for ref in eval_spec.get("reference_images") or []:
        ref_id = str(ref.get("id", "")).strip()
        ref_path = Path(str(ref.get("path", "")).strip())
        if not ref_id or not ref_path.exists():
            raise FileNotFoundError(f"Missing reference image for {eval_spec.get('case_id')}: {ref}")
        reference_images.append({"id": ref_id, "path": str(ref_path)})
        ref_bytes = ref_path.read_bytes()
        if not looks_like_supported_image(ref_bytes):
            raise RuntimeError(f"Reference image is not a supported raster image: {ref_path}")
        parts.extend([{"text": f"Reference image {ref_id}:"}, _inline_image_part(ref_bytes)])

    instruction = _build_official_eval_instruction(eval_spec=eval_spec, reference_images=reference_images)
    parts.append({"text": json.dumps(instruction, ensure_ascii=False, indent=2)})
    response = request_with_retry(
        lambda: requests.post(
            f"{settings.judge.base_url.rstrip('/')}{JD_CLOUD_RESPONSES_ENDPOINT}",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.judge.api_key}",
                "Trace-Id": str(uuid.uuid4()),
            },
            json={
                "model": settings.judge.model_name,
                "stream": False,
                "contents": {
                    "role": "user",
                    "parts": parts,
                },
                "generationConfig": {
                    "temperature": 0.0,
                    "responseMimeType": "application/json",
                },
            },
            timeout=settings.judge.request_timeout,
        )
    )
    try:
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Official evaluator request failed {response.status_code}: {response.text[:500]}") from exc
    raw_text = _extract_jdcloud_text(response.json())
    return _loads_json_payload(raw_text)


def _build_official_eval_instruction(*, eval_spec: dict[str, Any], reference_images: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "task": "Official Gen-Arena evaluation for one generated image.",
        "case_id": eval_spec.get("case_id"),
        "prompt": eval_spec.get("prompt", ""),
        "reference_images": reference_images,
        "entities": eval_spec.get("entities", []),
        "constraints": eval_spec.get("constraints", []),
        "evaluation_protocol": [
            "Judge visual intent fulfillment, not aesthetic quality.",
            "Evaluate every entity first. An entity passes only if it is visible and correctly realized.",
            "For every entity with reference_ids, evaluate it against the corresponding reference image or images, not only against the text prompt.",
            "A referenced entity passes only if the generated image preserves the required identity or appearance cues from the reference images.",
            "Fail a referenced entity when it is a generic substitute or mismatches the reference identity, outfit, character design, or distinctive appearance.",
            "Evaluate every constraint directly against the generated image. Use pass only when the visual evidence clearly supports the constraint.",
            "Use fail when the image contradicts the item or the required content is absent.",
            "Use fail when the item cannot be judged confidently from the image.",
            "Do not give credit for generic substitutes when a named identity, exact text, result, relation, or layout is required.",
        ],
        "output_schema": {
            "entities": [
                {
                    "id": "entity id",
                    "verdict": "pass|fail",
                    "reason": "brief reason",
                    "evidence": "visible evidence",
                }
            ],
            "constraints": [
                {
                    "id": "constraint id",
                    "verdict": "pass|fail",
                    "reason": "brief reason",
                    "evidence": "visible evidence",
                }
            ],
        },
        "requirements": [
            "Return valid JSON only.",
            "Return exactly one item for every entity id and every constraint id.",
            "Do not omit items.",
            "Do not add ids not listed in the evaluation specification.",
        ],
    }


def _collect_case_bindings(args: argparse.Namespace) -> list[CaseBinding]:
    repo_root = _repo_root()
    if args.eval_json and args.image_path:
        eval_json = _resolve_existing_path(args.eval_json, base=repo_root)
        image_path = _resolve_existing_path(args.image_path, base=repo_root)
        case_id = str(json.loads(eval_json.read_text(encoding="utf-8")).get("case_id") or eval_json.parent.name)
        return [CaseBinding(case_id=case_id, run_dir=image_path.parent, eval_json=eval_json, image_path=image_path)]

    if not args.run_root:
        raise ValueError("Batch official evaluation requires --run-root, or use --eval-json with --image-path.")
    run_root = _resolve_existing_path(args.run_root, base=repo_root)
    materialized_root = _resolve_existing_path(args.materialized_root, base=repo_root)
    selected_ids = {item.strip() for item in str(args.case_ids or "").split(",") if item.strip()}
    bindings: list[CaseBinding] = []
    for run_dir in sorted(path for path in run_root.iterdir() if path.is_dir()):
        case_id = run_dir.name
        if selected_ids and case_id not in selected_ids:
            continue
        if not any((run_dir / artifact).exists() for artifact in ("metrics.json", "finalization.json", "state.json")):
            continue
        eval_json = materialized_root / case_id / "benchmark_eval.json"
        if not eval_json.exists():
            eval_json = _eval_json_from_binding(run_dir)
        image_path = _find_generated_image(run_dir)
        bindings.append(CaseBinding(case_id=case_id, run_dir=run_dir, eval_json=eval_json, image_path=image_path))
    return bindings


def _eval_json_from_binding(run_dir: Path) -> Path:
    binding_path = run_dir / "benchmark_binding.json"
    if not binding_path.exists():
        raise FileNotFoundError(f"Missing benchmark_eval.json and benchmark_binding.json for run: {run_dir}")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    eval_path = Path(str(binding.get("eval_packet_json") or ""))
    if not eval_path.exists():
        raise FileNotFoundError(f"benchmark_binding.json points to missing eval packet: {eval_path}")
    return eval_path


def _find_generated_image(run_dir: Path) -> Path:
    candidates: list[str] = []
    for artifact in ("metrics.json", "finalization.json", "state.json"):
        path = run_dir / artifact
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("best_image_path", "latest_image_path", "last_image_path", "image_path"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    for value in candidates:
        resolved = _resolve_maybe_existing_path(value, base=run_dir)
        if resolved is not None:
            return resolved
    images = sorted(
        list(run_dir.glob("iteration_*.image.png"))
        + list(run_dir.glob("iteration_*.image.jpg"))
        + list(run_dir.glob("iteration_*.image.jpeg"))
        + list(run_dir.glob("iteration_*.image.webp")),
        key=lambda item: (item.stat().st_mtime, item.name),
    )
    if images:
        return images[-1]
    raise FileNotFoundError(f"No generated image found in run directory: {run_dir}")


def _normalize_item_results(raw_items: object, expected_ids: Any) -> dict[str, dict[str, str]]:
    expected = [str(item) for item in expected_ids]
    results: dict[str, dict[str, str]] = {}
    if isinstance(raw_items, dict):
        iterable = [{"id": key, **value} if isinstance(value, dict) else {"id": key, "verdict": value} for key, value in raw_items.items()]
    elif isinstance(raw_items, list):
        iterable = raw_items
    else:
        iterable = []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if item_id not in expected:
            continue
        verdict = _normalize_verdict(item.get("verdict"))
        results[item_id] = {
            "verdict": verdict,
            "reason": str(item.get("reason") or "").strip(),
            "evidence": str(item.get("evidence") or "").strip(),
        }
    for item_id in expected:
        results.setdefault(
            item_id,
            {
                "verdict": FAIL_VERDICT,
                "reason": "Verifier omitted this item.",
                "evidence": "",
            },
        )
    return results


def _normalize_verdict(value: object) -> str:
    text = str(value or "").strip().lower()
    if text == "pass":
        return PASS_VERDICT
    return FAIL_VERDICT


def _loads_json_payload(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    payload = json.loads(text)
    if isinstance(payload, list):
        return {"constraints": payload, "entities": []}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Official evaluator returned JSON that is not an object: {raw_text[:500]}")
    return payload


def _inline_image_part(image_bytes: bytes) -> dict[str, Any]:
    return {
        "inlineData": {
            "mimeType": _guess_mime_type(image_bytes),
            "data": base64.b64encode(image_bytes).decode("utf-8"),
        }
    }


def _resolve_output_dir(value: str) -> Path:
    if value:
        return Path(value)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("benchmark") / "evaluations" / f"gen_arena_official_{stamp}"


def _resolve_existing_path(value: str, *, base: Path) -> Path:
    path = _resolve_maybe_existing_path(value, base=base)
    if path is None:
        raise FileNotFoundError(value)
    return path


def _resolve_maybe_existing_path(value: str, *, base: Path) -> Path | None:
    raw = Path(value)
    candidates = [raw]
    if not raw.is_absolute():
        candidates.extend([base / raw, _repo_root() / raw, Path.cwd() / raw])
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _aggregate_rate(rows: list[dict[str, Any]], pass_key: str, total_key: str) -> float:
    total = sum(int(row.get(total_key, 0) or 0) for row in rows)
    passed = sum(int(row.get(pass_key, 0) or 0) for row in rows)
    return _safe_div(passed, total)


def _write_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    rows: list[dict[str, Any]] = [
        {
            "scope": "overall",
            "name": "overall",
            "cases": summary.get("cases", 0),
            "egip": summary.get("egip", 0.0),
            "entity_pass_rate": summary.get("entity_pass_rate", 0.0),
            "constraint_direct_pass_rate": summary.get("constraint_direct_pass_rate", 0.0),
            "constraint_gated_pass_rate": summary.get("constraint_gated_pass_rate", 0.0),
        }
    ]
    for category, data in (summary.get("by_category") or {}).items():
        rows.append({"scope": "category", "name": category, **data})
    for constraint_type, data in (summary.get("by_constraint_type") or {}).items():
        rows.append(
            {
                "scope": "constraint_type",
                "name": constraint_type,
                "cases": "",
                "egip": "",
                "entity_pass_rate": "",
                "constraint_direct_pass_rate": data.get("direct_pass_rate", 0.0),
                "constraint_gated_pass_rate": data.get("gated_pass_rate", 0.0),
                "total": data.get("total", 0),
            }
        )
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
