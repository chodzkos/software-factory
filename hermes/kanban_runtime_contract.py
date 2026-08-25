from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RuntimeExpectation:
    assignee: str
    workspace_kind: str
    workspace_path: str | None
    branch_name: str | None
    max_retries: int
    max_runtime: int | None = None
    parents: tuple[str, ...] = ()


def _parents(actual: Mapping[str, Any]) -> tuple[str, ...]:
    raw = actual.get("parents")
    if raw is None:
        raw = actual.get("parent_ids", ())
    if raw is None:
        return ()
    return tuple(str(value) for value in raw)


def validate_runtime(
    actual: Mapping[str, Any], expectation: RuntimeExpectation
) -> list[str]:
    """Zwróć listę driftów między oczekiwanym a faktycznym taskiem Hermesa."""

    errors: list[str] = []
    checks = {
        "assignee": expectation.assignee,
        "workspace_kind": expectation.workspace_kind,
        "workspace_path": expectation.workspace_path,
        "branch_name": expectation.branch_name,
        "max_retries": expectation.max_retries,
    }

    for field, expected in checks.items():
        actual_value = actual.get(field)
        if actual_value != expected:
            errors.append(f"{field}: expected={expected!r} actual={actual_value!r}")

    if expectation.max_runtime is not None:
        actual_runtime = actual.get("max_runtime")
        if actual_runtime is None:
            actual_runtime = actual.get("max_runtime_seconds")
        if actual_runtime != expectation.max_runtime:
            errors.append(
                "max_runtime: "
                f"expected={expectation.max_runtime!r} actual={actual_runtime!r}"
            )

    actual_parents = _parents(actual)
    if actual_parents != expectation.parents:
        errors.append(
            f"parents: expected={expectation.parents!r} actual={actual_parents!r}"
        )

    return errors


def validate_review_handoff(
    implementation: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    implementer_profile: str,
    reviewer_profile: str,
) -> list[str]:
    """Zweryfikuj, że reviewer czyta dokładnie artefakt worktree implementera."""

    errors: list[str] = []

    if implementer_profile == reviewer_profile:
        errors.append("implementer_and_reviewer_must_differ")

    if implementation.get("workspace_kind") != "worktree":
        errors.append("implementation_workspace_kind_must_be_worktree")

    resolved_path = implementation.get("resolved_workspace_path")
    if resolved_path is None:
        resolved_path = implementation.get("workspace_resolved_path")
    if resolved_path is None:
        resolved_path = implementation.get("resolved_path")

    if not resolved_path:
        errors.append("implementation_resolved_worktree_missing")
        return errors

    if review.get("workspace_kind") != "dir":
        errors.append(
            "review_workspace_kind: expected='dir' "
            f"actual={review.get('workspace_kind')!r}"
        )

    review_path = review.get("workspace_path")
    if review_path != resolved_path:
        errors.append(
            f"review_workspace_path: expected={resolved_path!r} actual={review_path!r}"
        )

    implementation_id = implementation.get("id")
    if implementation_id:
        review_parents = _parents(review)
        if review_parents != (str(implementation_id),):
            errors.append(
                "review_parents: "
                f"expected={(str(implementation_id),)!r} actual={review_parents!r}"
            )

    return errors


def validate_task_graph(
    actual: Mapping[str, Any],
    expectation: RuntimeExpectation,
    *,
    implementation: Mapping[str, Any] | None = None,
    implementer_profile: str | None = None,
    reviewer_profile: str | None = None,
) -> list[str]:
    """Jeden fail-closed punkt walidacji pojedynczego tasku lub review handoffu."""

    errors = validate_runtime(actual, expectation)
    if implementation is not None:
        if not implementer_profile or not reviewer_profile:
            errors.append("review_profiles_required")
        else:
            errors.extend(
                validate_review_handoff(
                    implementation,
                    actual,
                    implementer_profile=implementer_profile,
                    reviewer_profile=reviewer_profile,
                )
            )
    return errors


def format_drift(errors: Sequence[str]) -> str:
    if not errors:
        return "RUNTIME_CONTRACT_OK"
    return "RUNTIME_CONTRACT_DRIFT: " + "; ".join(errors)
