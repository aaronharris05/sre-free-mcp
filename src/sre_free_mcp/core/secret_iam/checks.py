"""Pure-logic rules for Secret Manager + IAM audits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sre_free_mcp.core.models import Finding


@dataclass(frozen=True)
class SecretSnapshot:
    """One secret's worth of evidence."""

    name: str                               # short name, not the full /versions/... path
    latest_enabled_version_created_at: datetime | None
    enabled_version_count: int


# Roles that confer broad project mutation. Granting these to an
# end-user identity is the textbook IAM smell.
_OVERPRIVILEGED_ROLES = {
    "roles/owner",
    "roles/editor",
    "roles/iam.securityAdmin",
}

# Members that, when granted ANY role, make the project effectively
# public.
_PUBLIC_MEMBERS = {"allUsers", "allAuthenticatedUsers"}


def evaluate_secret(
    snapshot: SecretSnapshot,
    now: datetime,
    *,
    rotation_threshold_days: int = 90,
) -> list[Finding]:
    """Apply rotation + emptiness rules to one secret snapshot."""
    findings: list[Finding] = []
    base = {"secret": snapshot.name}

    if snapshot.enabled_version_count == 0:
        findings.append(
            Finding(
                scope="secret_iam",
                scope_id=f"secret:{snapshot.name}",
                gap_kind="no_active_secret_version",
                severity="high",
                details={
                    **base,
                    "rule": "secret has no enabled version; any reader will fail",
                },
            )
        )
        return findings

    if snapshot.latest_enabled_version_created_at is None:
        return findings  # shouldn't happen if count > 0, but be defensive

    age = now - snapshot.latest_enabled_version_created_at
    if age > timedelta(days=rotation_threshold_days):
        findings.append(
            Finding(
                scope="secret_iam",
                scope_id=f"secret:{snapshot.name}",
                gap_kind="unrotated_secret",
                severity="medium",
                details={
                    **base,
                    "age_days": age.days,
                    "threshold_days": rotation_threshold_days,
                    "latest_version_created_at": snapshot.latest_enabled_version_created_at.isoformat(),
                    "rule": f"latest enabled version is > {rotation_threshold_days}d old",
                },
            )
        )

    return findings


@dataclass(frozen=True)
class IamBinding:
    role: str
    members: tuple[str, ...]


def evaluate_iam_policy(
    bindings: list[IamBinding],
    *,
    project_id: str,
) -> list[Finding]:
    """Apply IAM-hygiene rules to a project's IAM bindings."""
    findings: list[Finding] = []

    for binding in bindings:
        # Public members on any role.
        public_members = [m for m in binding.members if m in _PUBLIC_MEMBERS]
        for member in public_members:
            findings.append(
                Finding(
                    scope="secret_iam",
                    scope_id=f"iam:{project_id}:{binding.role}",
                    gap_kind="public_iam_binding",
                    severity="critical",
                    details={
                        "project_id": project_id,
                        "role": binding.role,
                        "member": member,
                        "rule": f"{member} grants this role to the whole world",
                    },
                )
            )

        # Overprivileged roles granted to user-typed principals
        # (anything not a service account or Google-managed group).
        if binding.role in _OVERPRIVILEGED_ROLES:
            for member in binding.members:
                if member in _PUBLIC_MEMBERS:
                    continue  # already flagged above
                if member.startswith("user:") or member.startswith("group:"):
                    findings.append(
                        Finding(
                            scope="secret_iam",
                            scope_id=f"iam:{project_id}:{binding.role}:{member}",
                            gap_kind="overprivileged_iam_role",
                            severity="high",
                            details={
                                "project_id": project_id,
                                "role": binding.role,
                                "member": member,
                                "rule": f"{binding.role} on user/group principal; prefer "
                                "least-privilege custom role",
                            },
                        )
                    )

    return findings


__all__ = [
    "IamBinding",
    "SecretSnapshot",
    "evaluate_iam_policy",
    "evaluate_secret",
]
