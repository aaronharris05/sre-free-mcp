"""Unit tests for secret_iam rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sre_free_mcp.core.secret_iam.checks import (
    IamBinding,
    SecretSnapshot,
    evaluate_iam_policy,
    evaluate_secret,
)

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


def _snap(
    name: str = "smtp-password",
    *,
    latest: datetime | None = None,
    count: int = 1,
) -> SecretSnapshot:
    return SecretSnapshot(
        name=name,
        latest_enabled_version_created_at=latest,
        enabled_version_count=count,
    )


# ---------------------------------------------------------------------------
# Secret rules
# ---------------------------------------------------------------------------


def test_no_active_version_fires_high():
    findings = evaluate_secret(_snap(count=0, latest=None), _NOW)
    assert any(
        f.gap_kind == "no_active_secret_version" and f.severity == "high"
        for f in findings
    )


def test_freshly_rotated_secret_does_not_fire():
    findings = evaluate_secret(_snap(latest=_NOW - timedelta(days=10)), _NOW)
    assert findings == []


def test_unrotated_secret_fires_medium():
    findings = evaluate_secret(_snap(latest=_NOW - timedelta(days=200)), _NOW)
    matched = [f for f in findings if f.gap_kind == "unrotated_secret"]
    assert len(matched) == 1
    assert matched[0].severity == "medium"


def test_rotation_threshold_is_tunable():
    snap = _snap(latest=_NOW - timedelta(days=45))
    assert evaluate_secret(snap, _NOW) == []  # 45d < default 90d
    findings = evaluate_secret(snap, _NOW, rotation_threshold_days=30)
    assert any(f.gap_kind == "unrotated_secret" for f in findings)


# ---------------------------------------------------------------------------
# IAM rules
# ---------------------------------------------------------------------------


def test_public_member_fires_critical():
    bindings = [IamBinding(role="roles/storage.objectViewer", members=("allUsers",))]
    findings = evaluate_iam_policy(bindings, project_id="p")
    matched = [f for f in findings if f.gap_kind == "public_iam_binding"]
    assert len(matched) == 1
    assert matched[0].severity == "critical"


def test_all_authenticated_users_also_fires_public():
    bindings = [
        IamBinding(role="roles/bigquery.dataViewer", members=("allAuthenticatedUsers",))
    ]
    findings = evaluate_iam_policy(bindings, project_id="p")
    assert any(f.gap_kind == "public_iam_binding" for f in findings)


def test_overprivileged_owner_role_on_user_fires_high():
    bindings = [IamBinding(role="roles/owner", members=("user:alice@example.com",))]
    findings = evaluate_iam_policy(bindings, project_id="p")
    matched = [f for f in findings if f.gap_kind == "overprivileged_iam_role"]
    assert len(matched) == 1
    assert matched[0].severity == "high"


def test_owner_role_on_service_account_does_not_fire_overprivileged():
    """SAs with Owner is a different smell — tracked elsewhere."""
    bindings = [
        IamBinding(
            role="roles/owner",
            members=("serviceAccount:terraform@p.iam.gserviceaccount.com",),
        )
    ]
    findings = evaluate_iam_policy(bindings, project_id="p")
    assert not any(f.gap_kind == "overprivileged_iam_role" for f in findings)


def test_editor_role_on_group_fires_overprivileged():
    bindings = [IamBinding(role="roles/editor", members=("group:devs@example.com",))]
    findings = evaluate_iam_policy(bindings, project_id="p")
    assert any(f.gap_kind == "overprivileged_iam_role" for f in findings)


def test_non_overprivileged_role_does_not_fire():
    bindings = [IamBinding(role="roles/viewer", members=("user:alice@example.com",))]
    findings = evaluate_iam_policy(bindings, project_id="p")
    assert findings == []


def test_findings_use_scope_secret_iam():
    bindings = [IamBinding(role="roles/owner", members=("user:a@b.com",))]
    findings = evaluate_iam_policy(bindings, project_id="p")
    assert all(f.scope == "secret_iam" for f in findings)
