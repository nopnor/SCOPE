from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping


EntityPriority = Literal["primary", "supporting", "peripheral"]
ConstraintType = Literal["attribute", "count", "relation", "layout", "style", "text"]
ConstraintPriority = Literal["critical", "major", "minor"]
UnknownOwnerKind = Literal["object", "constraint", "prompt"]
UnknownKind = Literal["external_reference", "semantic_reasoning"]
UnknownStatus = Literal["open", "resolved", "consumed"]
ResolutionStage = Literal["retrieve", "reason"]
EvidenceKind = Literal["web", "image"]
ReviewVerdict = Literal["pass", "fail", "uncertain"]
RepairAction = Literal["none", "rewrite_prompt", "image_edit", "regenerate"]
RepairFamily = Literal[
    "subject_repair",
    "text_repair",
    "relation_repair",
    "count_repair",
    "attribute_repair",
    "layout_repair",
    "style_repair",
]

ALLOWED_ENTITY_PRIORITIES = {"primary", "supporting", "peripheral"}
ALLOWED_CONSTRAINT_TYPES = {"attribute", "count", "relation", "layout", "style", "text"}
ALLOWED_CONSTRAINT_PRIORITIES = {"critical", "major", "minor"}
ALLOWED_UNKNOWN_OWNER_KINDS = {"object", "constraint", "prompt"}
ALLOWED_UNKNOWN_KINDS = {"external_reference", "semantic_reasoning"}
ALLOWED_UNKNOWN_STATUSES = {"open", "resolved", "consumed"}
ALLOWED_RESOLUTION_STAGES = {"retrieve", "reason"}
ALLOWED_EVIDENCE_KINDS = {"web", "image"}
ALLOWED_REPAIR_ACTIONS = {"none", "rewrite_prompt", "image_edit", "regenerate"}
ALLOWED_REPAIR_FAMILIES = {
    "subject_repair",
    "text_repair",
    "relation_repair",
    "count_repair",
    "attribute_repair",
    "layout_repair",
    "style_repair",
}


@dataclass
class ScopeEntity:
    id: str
    name: str
    priority: EntityPriority = "supporting"


@dataclass
class ScopeConstraint:
    id: str
    text: str
    type: ConstraintType
    priority: ConstraintPriority = "major"
    spec: dict = field(default_factory=dict)


@dataclass
class ScopeUnknown:
    id: str
    kind: UnknownKind
    owner_id: str
    owner_kind: UnknownOwnerKind
    question: str
    owner_name: str = ""
    status: UnknownStatus = "open"
    source: str = "decompose"
    resolved_by: str = ""


@dataclass
class ScopeRetrievalEvidence:
    kind: EvidenceKind
    title: str
    url: str
    snippet: str = ""
    query: str = ""
    local_path: str = ""


@dataclass
class ScopeUnknownResolution:
    unknown_id: str
    note: str
    owner_id: str
    owner_kind: UnknownOwnerKind
    kind: UnknownKind
    stage: ResolutionStage
    owner_name: str = ""
    question: str = ""
    evidence: list[ScopeRetrievalEvidence] = field(default_factory=list)


@dataclass
class ScopeDecomposition:
    entities: list[ScopeEntity] = field(default_factory=list)
    constraints: list[ScopeConstraint] = field(default_factory=list)
    unknowns: list[ScopeUnknown] = field(default_factory=list)


@dataclass
class ScopeReviewResult:
    id: str
    verdict: ReviewVerdict
    reason: str
    item_kind: str = "constraint"
    confidence: float | None = None
    evidence: str = ""
    target_id: str = ""
    owner_id: str = ""
    failure_family: RepairFamily | str = ""
    blocked_by: str = ""


@dataclass
class ScopeVerificationOutcome:
    review_results: list[ScopeReviewResult] = field(default_factory=list)
    new_unknowns: list[ScopeUnknown] = field(default_factory=list)


@dataclass
class ScopeBestVerification:
    iteration: int = 0
    image_path: str = ""
    pass_count: int = 0
    fail_count: int = 0
    uncertain_count: int = 0
    total: int = 0
    new_unknown_count: int = 0
    source: str = ""
    backend: str = ""


@dataclass
class ScopeRepairPatch:
    skill: RepairFamily
    targets: list[str] = field(default_factory=list)
    additions: list[str] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)
    removals: list[str] = field(default_factory=list)
    recommended_action: Literal["rewrite_prompt", "image_edit", "regenerate"] = "rewrite_prompt"
    reason: str = ""
    diagnosis: str = ""


@dataclass
class ScopeRepairDecision:
    selected_review_ids: list[str] = field(default_factory=list)
    repair_action: RepairAction = "none"
    repair_patch: ScopeRepairPatch | None = None


@dataclass
class ScopeSynthesisPlan:
    final_prompt: str
    synthesis_notes: list[str] = field(default_factory=list)


@dataclass
class ScopeRepairUpdate:
    decision: ScopeRepairDecision
    updated_final_prompt: str = ""


@dataclass
class ScopeArtifact:
    kind: str
    path: str


@dataclass
class ScopeCaseState:
    prompt: str
    model_name: str = "SCOPE"
    case_id: str = ""
    input_images: list[str] = field(default_factory=list)
    reference_images: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    benchmark: dict = field(default_factory=dict)
    iteration: int = 0
    entities: list[ScopeEntity] = field(default_factory=list)
    constraints: list[ScopeConstraint] = field(default_factory=list)
    unknowns: list[ScopeUnknown] = field(default_factory=list)
    retrieval_resolutions: list[ScopeUnknownResolution] = field(default_factory=list)
    reasoning_resolutions: list[ScopeUnknownResolution] = field(default_factory=list)
    final_prompt: str = ""
    review_results: list[ScopeReviewResult] = field(default_factory=list)
    verification_unknowns: list[ScopeUnknown] = field(default_factory=list)
    repair_action: RepairAction = "none"
    repair_decision: ScopeRepairDecision | None = None
    last_image_path: str = ""
    best_image_path: str = ""
    best_iteration: int = 0
    best_verification: ScopeBestVerification | None = None
    run_config: dict = field(default_factory=dict)
    stage_trace: list[str] = field(default_factory=list)
    artifacts: list[ScopeArtifact] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def add_artifact(self, kind: str, path: Path) -> None:
        resolved = str(path)
        if any(item.kind == kind and item.path == resolved for item in self.artifacts):
            return
        self.artifacts.append(ScopeArtifact(kind=kind, path=resolved))

    @classmethod
    def from_dict(cls, data: dict) -> "ScopeCaseState":
        parsed_entities = [
            ScopeEntity(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                priority=item.get("priority", "supporting"),
            )
            for item in data.get("entities", [])
            if isinstance(item, dict)
        ]
        parsed_constraints = [
            ScopeConstraint(
                id=str(item.get("id", "")),
                text=str(item.get("text", "")),
                type=item.get("type", "attribute"),
                priority=item.get("priority", "major"),
                spec=dict(item.get("spec", {})) if isinstance(item.get("spec", {}), dict) else {},
            )
            for item in data.get("constraints", [])
            if isinstance(item, dict)
        ]
        entities_by_id = {item.id: item for item in parsed_entities}
        constraint_ids = {item.id for item in parsed_constraints if str(item.id).strip()}
        parsed_unknowns = parse_unknowns_payload(
            data.get("unknowns"),
            field_name="unknowns",
            entities_by_id=entities_by_id,
            constraint_ids=constraint_ids,
            default_source="decompose",
        )
        state = cls(
            prompt=str(data.get("prompt", "")),
            model_name=str(data.get("model_name", "SCOPE")),
            case_id=str(data.get("case_id", "")),
            input_images=[str(item) for item in data.get("input_images", [])],
            reference_images=[str(item) for item in data.get("reference_images", [])],
            checklist=[str(item) for item in data.get("checklist", [])],
            benchmark=dict(data.get("benchmark", {})) if isinstance(data.get("benchmark", {}), dict) else {},
            iteration=int(data.get("iteration", 0) or 0),
            entities=parsed_entities,
            constraints=parsed_constraints,
            unknowns=parsed_unknowns,
            retrieval_resolutions=parse_retrieval_payload(
                {"retrieval_resolutions": data.get("retrieval_resolutions", [])},
                available_unknowns=parsed_unknowns,
                require_non_empty=False,
                allow_non_open=True,
            ),
            reasoning_resolutions=parse_reasoning_payload(
                {"reasoning_resolutions": data.get("reasoning_resolutions", [])},
                available_unknowns=parsed_unknowns,
                require_non_empty=False,
                allow_non_open=True,
            ),
            final_prompt=str(data.get("final_prompt", "")),
            review_results=_parse_review_results(
                data.get("review_results"),
                field_name="review_results",
                checklist=[str(item) for item in data.get("checklist", [])],
                require_non_empty=False,
            ),
            verification_unknowns=parse_unknowns_payload(
                data.get("verification_unknowns"),
                field_name="verification_unknowns",
                entities_by_id=entities_by_id,
                constraint_ids=constraint_ids,
                default_source="verify",
            ),
            repair_action=data.get("repair_action", "none"),
            repair_decision=_parse_repair_decision(
                data.get("repair_decision"),
                entities_by_id=entities_by_id,
                constraint_ids=constraint_ids,
            ),
            last_image_path=str(data.get("last_image_path", "")),
            best_image_path=str(data.get("best_image_path", "")),
            best_iteration=int(data.get("best_iteration", 0) or 0),
            best_verification=_parse_best_verification(data.get("best_verification")),
            run_config=dict(data.get("run_config", {})) if isinstance(data.get("run_config", {}), dict) else {},
            stage_trace=[str(item) for item in data.get("stage_trace", [])],
            artifacts=[
                ScopeArtifact(
                    kind=str(item.get("kind", "")),
                    path=str(item.get("path", "")),
                )
                for item in data.get("artifacts", [])
                if isinstance(item, dict)
            ],
        )
        return state


def parse_decomposition_payload(value: object) -> ScopeDecomposition:
    if not isinstance(value, Mapping):
        raise ValueError("decomposition payload must be a JSON object")

    entities = _parse_entities(value.get("entities"))
    constraints = _parse_constraints(value.get("constraints"))
    unknowns = parse_unknowns_payload(
        value.get("unknowns"),
        field_name="unknowns",
        entities_by_id={item.id: item for item in entities},
        constraint_ids={item.id for item in constraints},
        default_source="decompose",
    )
    return ScopeDecomposition(
        entities=entities,
        constraints=constraints,
        unknowns=unknowns,
    )


def parse_reasoning_payload(
    value: object,
    *,
    available_unknowns: list[ScopeUnknown],
    require_non_empty: bool = True,
    allow_non_open: bool = False,
) -> list[ScopeUnknownResolution]:
    return _parse_unknown_resolutions(
        value,
        field_name="reasoning_resolutions",
        available_unknowns=available_unknowns,
        stage="reason",
        require_non_empty=require_non_empty,
        allow_non_open=allow_non_open,
    )


def parse_retrieval_payload(
    value: object,
    *,
    available_unknowns: list[ScopeUnknown],
    require_non_empty: bool = True,
    allow_non_open: bool = False,
) -> list[ScopeUnknownResolution]:
    return _parse_unknown_resolutions(
        value,
        field_name="retrieval_resolutions",
        available_unknowns=available_unknowns,
        stage="retrieve",
        require_non_empty=require_non_empty,
        allow_non_open=allow_non_open,
    )


def parse_synthesis_payload(value: object) -> ScopeSynthesisPlan:
    if not isinstance(value, Mapping):
        raise ValueError("synthesis payload must be a JSON object")
    final_prompt = _require_text(value.get("final_prompt"), field_name="final_prompt")
    notes = [
        _require_text(item, field_name=f"synthesis_notes[{index}]")
        for index, item in enumerate(_require_list(value.get("synthesis_notes"), field_name="synthesis_notes"), start=1)
    ]
    return ScopeSynthesisPlan(final_prompt=final_prompt, synthesis_notes=notes)


def parse_verification_payload(
    value: object,
    *,
    entities: list[ScopeEntity],
    constraints: list[ScopeConstraint],
    checklist: list[str] | None = None,
) -> ScopeVerificationOutcome:
    if not isinstance(value, Mapping):
        raise ValueError("verification payload must be a JSON object")
    review_results = _parse_review_results(
        value.get("review_results"),
        field_name="review_results",
        checklist=checklist or [],
        require_non_empty=True,
    )
    new_unknowns = parse_unknowns_payload(
        value.get("new_unknowns"),
        field_name="new_unknowns",
        entities_by_id={item.id: item for item in entities},
        constraint_ids={item.id for item in constraints if str(item.id).strip()},
        default_source="verify",
    )
    return ScopeVerificationOutcome(review_results=review_results, new_unknowns=new_unknowns)


def _parse_best_verification(value: object) -> ScopeBestVerification | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("best_verification must be a JSON object when provided")
    return ScopeBestVerification(
        iteration=int(value.get("iteration", 0) or 0),
        image_path=str(value.get("image_path", "")),
        pass_count=int(value.get("pass_count", 0) or 0),
        fail_count=int(value.get("fail_count", 0) or 0),
        uncertain_count=int(value.get("uncertain_count", 0) or 0),
        total=int(value.get("total", 0) or 0),
        new_unknown_count=int(value.get("new_unknown_count", 0) or 0),
        source=str(value.get("source", "")),
        backend=str(value.get("backend", "")),
    )


def parse_repair_payload(
    value: object,
    *,
    available_review_ids: set[str] | None = None,
) -> ScopeRepairUpdate:
    if not isinstance(value, Mapping):
        raise ValueError("repair payload must be a JSON object")

    selected_review_ids = _parse_string_list(value.get("selected_review_ids"), field_name="selected_review_ids")
    repair_action = _require_enum(
        value.get("repair_action"),
        allowed=ALLOWED_REPAIR_ACTIONS,
        field_name="repair_action",
    )
    repair_patch = _parse_repair_patch(value.get("repair_patch"))
    updated_final_prompt = _clean_text(value.get("updated_final_prompt"))

    if available_review_ids is not None:
        unknown_review_ids = [item for item in selected_review_ids if item not in available_review_ids]
        if unknown_review_ids:
            raise ValueError(f"repair payload references unknown review ids: {', '.join(sorted(unknown_review_ids))}")

    if repair_action == "none":
        if selected_review_ids:
            raise ValueError("repair payload with action 'none' must not select review ids")
        if updated_final_prompt:
            raise ValueError("repair payload with action 'none' must not include updated_final_prompt")
    else:
        if not selected_review_ids:
            raise ValueError("repair payload must select at least one review id when repair_action is not 'none'")
        if repair_action == "rewrite_prompt" and not updated_final_prompt:
            raise ValueError("repair payload with action 'rewrite_prompt' must include updated_final_prompt")
        if repair_patch is None:
            raise ValueError("repair payload with action other than 'none' must include repair_patch")
        if not repair_patch.targets:
            raise ValueError("repair payload repair_patch.targets must be non-empty when repair_action is not 'none'")
        missing_targets = [item for item in repair_patch.targets if item not in selected_review_ids]
        if missing_targets:
            raise ValueError(
                f"repair payload repair_patch.targets must be contained in selected_review_ids: {', '.join(sorted(missing_targets))}"
            )
        if not _clean_text(repair_patch.diagnosis):
            raise ValueError("repair payload repair_patch.diagnosis must be non-empty when repair_action is not 'none'")

    return ScopeRepairUpdate(
        decision=ScopeRepairDecision(
            selected_review_ids=selected_review_ids,
            repair_action=repair_action,
            repair_patch=repair_patch,
        ),
        updated_final_prompt=updated_final_prompt,
    )


def parse_unknowns_payload(
    value: object,
    *,
    field_name: str,
    entities_by_id: Mapping[str, ScopeEntity],
    constraint_ids: set[str],
    default_source: str,
) -> list[ScopeUnknown]:
    items = _require_list(value, field_name=field_name)
    unknowns: list[ScopeUnknown] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name}[{index}] must be an object")
        unknown_id = _require_text(item.get("id"), field_name=f"{field_name}[{index}].id")
        kind = _require_enum(
            item.get("kind"),
            allowed=ALLOWED_UNKNOWN_KINDS,
            field_name=f"{field_name}[{index}].kind",
        )
        owner_kind = _enum_or_default(
            item.get("owner_kind"),
            allowed=ALLOWED_UNKNOWN_OWNER_KINDS,
            default="prompt",
            field_name=f"{field_name}[{index}].owner_kind",
        )
        owner_id = _clean_text(item.get("owner_id")) or ("p0" if owner_kind == "prompt" else "")
        question = _require_text(item.get("question"), field_name=f"{field_name}[{index}].question")
        owner_name = _clean_text(item.get("owner_name"))
        status = _enum_or_default(
            item.get("status"),
            allowed=ALLOWED_UNKNOWN_STATUSES,
            default="open",
            field_name=f"{field_name}[{index}].status",
        )
        resolved_by = _clean_text(item.get("resolved_by"))
        source = _clean_text(item.get("source")) or default_source

        if owner_kind == "object":
            entity = entities_by_id.get(owner_id)
            if entity is None:
                raise ValueError(f"{field_name}[{index}] references unknown object owner id: {owner_id}")
            owner_name = owner_name or entity.name
        elif owner_kind == "constraint":
            if owner_id not in constraint_ids:
                raise ValueError(f"{field_name}[{index}] references unknown constraint owner id: {owner_id}")
            owner_name = ""
        else:
            if owner_id != "p0":
                raise ValueError(f"{field_name}[{index}] prompt owner_id must be 'p0'")
            owner_name = ""

        if status == "open":
            resolved_by = ""
        elif resolved_by not in ALLOWED_RESOLUTION_STAGES:
            raise ValueError(f"{field_name}[{index}].resolved_by must be 'retrieve' or 'reason' for non-open unknowns")

        if unknown_id in seen_ids:
            raise ValueError(f"duplicate unknown id: {unknown_id}")
        seen_ids.add(unknown_id)
        unknowns.append(
            ScopeUnknown(
                id=unknown_id,
                kind=kind,
                owner_id=owner_id,
                owner_kind=owner_kind,
                question=question,
                owner_name=owner_name,
                status=status,
                source=source,
                resolved_by=resolved_by,
            )
        )
    return unknowns


def _parse_entities(value: object) -> list[ScopeEntity]:
    items = _require_list(value, field_name="entities")
    entities: list[ScopeEntity] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"entities[{index}] must be an object")
        entity_id = _require_text(item.get("id"), field_name=f"entities[{index}].id")
        name = _require_text(item.get("name"), field_name=f"entities[{index}].name")
        priority = _enum_or_default(
            item.get("priority"),
            allowed=ALLOWED_ENTITY_PRIORITIES,
            default="supporting",
            field_name=f"entities[{index}].priority",
        )
        if entity_id in seen_ids:
            raise ValueError(f"duplicate entity id: {entity_id}")
        seen_ids.add(entity_id)
        entities.append(ScopeEntity(id=entity_id, name=name, priority=priority))
    return entities


def _parse_constraints(value: object) -> list[ScopeConstraint]:
    items = _require_list(value, field_name="constraints")
    constraints: list[ScopeConstraint] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"constraints[{index}] must be an object")
        constraint_id = _require_text(item.get("id"), field_name=f"constraints[{index}].id")
        text = _require_text(item.get("text"), field_name=f"constraints[{index}].text")
        constraint_type = _require_enum(
            item.get("type"),
            allowed=ALLOWED_CONSTRAINT_TYPES,
            field_name=f"constraints[{index}].type",
        )
        priority = _enum_or_default(
            item.get("priority"),
            allowed=ALLOWED_CONSTRAINT_PRIORITIES,
            default="major",
            field_name=f"constraints[{index}].priority",
        )
        if constraint_id in seen_ids:
            raise ValueError(f"duplicate constraint id: {constraint_id}")
        seen_ids.add(constraint_id)
        constraints.append(
            ScopeConstraint(
                id=constraint_id,
                text=text,
                type=constraint_type,
                priority=priority,
                spec=dict(item.get("spec", {})) if isinstance(item.get("spec", {}), Mapping) else {},
            )
        )
    return constraints


def _parse_unknown_resolutions(
    value: object,
    *,
    field_name: str,
    available_unknowns: list[ScopeUnknown],
    stage: ResolutionStage,
    require_non_empty: bool,
    allow_non_open: bool,
) -> list[ScopeUnknownResolution]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name.split('_')[0]} payload must be a JSON object")
    items = _require_list(value.get(field_name), field_name=field_name)
    resolutions: list[ScopeUnknownResolution] = []
    available_by_id = {item.id: item for item in available_unknowns}
    seen_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name}[{index}] must be an object")
        unknown_id = _require_text(item.get("unknown_id"), field_name=f"{field_name}[{index}].unknown_id")
        note = _require_text(item.get("note"), field_name=f"{field_name}[{index}].note")
        unknown = available_by_id.get(unknown_id)
        if unknown is None:
            raise ValueError(f"{field_name}[{index}] references unknown unknown_id: {unknown_id}")
        if not allow_non_open and unknown.status != "open":
            raise ValueError(f"{field_name}[{index}] references unknown_id '{unknown_id}' that is not open")
        if stage == "retrieve" and unknown.kind != "external_reference":
            raise ValueError(f"{field_name}[{index}] must point to an external_reference unknown")
        if stage == "reason" and unknown.kind != "semantic_reasoning":
            raise ValueError(f"{field_name}[{index}] must point to a semantic_reasoning unknown")
        if unknown_id in seen_ids:
            raise ValueError(f"duplicate {field_name} unknown_id: {unknown_id}")
        seen_ids.add(unknown_id)
        resolutions.append(
            ScopeUnknownResolution(
                unknown_id=unknown.id,
                note=note,
                owner_id=unknown.owner_id,
                owner_kind=unknown.owner_kind,
                kind=unknown.kind,
                stage=stage,
                owner_name=unknown.owner_name,
                question=unknown.question,
                evidence=_parse_retrieval_evidence(item.get("evidence")) if stage == "retrieve" else [],
            )
        )
    if require_non_empty and not resolutions:
        raise ValueError(f"{field_name} must contain at least one resolution")
    return resolutions


def _parse_review_results(
    value: object,
    *,
    field_name: str,
    checklist: list[str],
    require_non_empty: bool,
) -> list[ScopeReviewResult]:
    items = _require_list(value, field_name=field_name)
    results: list[ScopeReviewResult] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name}[{index}] must be an object")
        verdict = _enum_or_default(
            item.get("verdict"),
            allowed={"pass", "fail", "uncertain"},
            default="uncertain",
            field_name=f"{field_name}[{index}].verdict",
        )
        default_reason = checklist[index - 1] if index - 1 < len(checklist) else f"review item {index}"
        confidence_value = item.get("confidence")
        confidence = float(confidence_value) if isinstance(confidence_value, (int, float)) else None
        results.append(
            ScopeReviewResult(
                id=_clean_text(item.get("id")) or f"check_{index}",
                verdict=verdict,  # type: ignore[arg-type]
                reason=_clean_text(item.get("reason")) or default_reason,
                item_kind=_clean_text(item.get("item_kind")) or "constraint",
                confidence=confidence,
                evidence=_clean_text(item.get("evidence")),
                target_id=_clean_text(item.get("target_id")),
                owner_id=_clean_text(item.get("owner_id")),
                failure_family=_enum_or_default(
                    item.get("failure_family"),
                    allowed=ALLOWED_REPAIR_FAMILIES,
                    default="",
                    field_name=f"{field_name}[{index}].failure_family",
                ),
                blocked_by=_clean_text(item.get("blocked_by")),
            )
        )
    if require_non_empty and not results:
        raise ValueError(f"{field_name} must contain at least one review_result")
    return results


def _parse_string_list(value: object, *, field_name: str) -> list[str]:
    items = _require_list(value, field_name=field_name)
    parsed: list[str] = []
    for index, item in enumerate(items, start=1):
        parsed.append(_require_text(item, field_name=f"{field_name}[{index}]"))
    return parsed


def _parse_retrieval_evidence(value: object) -> list[ScopeRetrievalEvidence]:
    items = _require_list(value, field_name="evidence")
    evidence: list[ScopeRetrievalEvidence] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"evidence[{index}] must be an object")
        kind = _require_enum(
            item.get("kind"),
            allowed=ALLOWED_EVIDENCE_KINDS,
            field_name=f"evidence[{index}].kind",
        )
        title = _require_text(item.get("title"), field_name=f"evidence[{index}].title")
        url = _require_text(item.get("url"), field_name=f"evidence[{index}].url")
        evidence.append(
            ScopeRetrievalEvidence(
                kind=kind,
                title=title,
                url=url,
                snippet=_clean_text(item.get("snippet")),
                query=_clean_text(item.get("query")),
                local_path=_clean_text(item.get("local_path")),
            )
        )
    return evidence


def _require_list(value: object, *, field_name: str) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def _require_text(value: object, *, field_name: str) -> str:
    text = _clean_text(value)
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _require_enum(value: object, *, allowed: set[str], field_name: str) -> str:
    text = _clean_text(value).lower()
    if text in allowed:
        return text
    raise ValueError(f"{field_name} must be one of: {', '.join(sorted(allowed))}")


def _enum_or_default(value: object, *, allowed: set[str], default: str, field_name: str) -> str:
    text = _clean_text(value).lower()
    if not text:
        return default
    if text in allowed:
        return text
    raise ValueError(f"{field_name} must be one of: {', '.join(sorted(allowed))}")


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _parse_repair_decision(
    value: object,
    *,
    entities_by_id: Mapping[str, ScopeEntity],
    constraint_ids: set[str],
) -> ScopeRepairDecision | None:
    return _parse_repair_decision_with_context(value, entities_by_id=entities_by_id, constraint_ids=constraint_ids)


def _parse_repair_decision_with_context(
    value: object,
    *,
    entities_by_id: Mapping[str, ScopeEntity],
    constraint_ids: set[str],
) -> ScopeRepairDecision | None:
    if not isinstance(value, dict):
        return None
    patch = _parse_repair_patch(value.get("repair_patch"))
    return ScopeRepairDecision(
        selected_review_ids=[str(item) for item in value.get("selected_review_ids", []) if str(item).strip()],
        repair_action=value.get("repair_action", "none"),
        repair_patch=patch,
    )


def _parse_repair_patch(value: object) -> ScopeRepairPatch | None:
    if not isinstance(value, Mapping):
        return None
    skill = _require_enum(
        value.get("skill"),
        allowed=ALLOWED_REPAIR_FAMILIES,
        field_name="repair_patch.skill",
    )
    recommended_action = _enum_or_default(
        value.get("recommended_action"),
        allowed=ALLOWED_REPAIR_ACTIONS - {"none"},
        default="rewrite_prompt",
        field_name="repair_patch.recommended_action",
    )
    return ScopeRepairPatch(
        skill=skill,
        targets=[str(item) for item in value.get("targets", []) if str(item).strip()],
        additions=[str(item) for item in value.get("additions", []) if str(item).strip()],
        clarifications=[str(item) for item in value.get("clarifications", []) if str(item).strip()],
        removals=[str(item) for item in value.get("removals", []) if str(item).strip()],
        recommended_action=recommended_action,
        reason=str(value.get("reason", "")),
        diagnosis=str(value.get("diagnosis", "")),
    )
