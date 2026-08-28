#!/usr/bin/env python
"""Synthetic staging/demo data seed for Solace-AI (referenced by docs/STAGING-BRINGUP.md).

This generates a small, fully SYNTHETIC dataset — demo users, therapy/diagnosis
sessions, safety assessments across every risk level (including at least one
CRITICAL crisis case), a Big-Five personality profile, and a few memory entries —
for staging bring-up and end-to-end verification.

Design
------
Generation is PURE and importable: every ``generate_*`` function builds real
service domain objects with NO database access, so they are unit-testable and
usable in a --dry-run. PERSISTENCE is a separate, optional step that writes to the
real database named by ``DATABASE_URL``.

Safety
------
* All data is SYNTHETIC — there is NO real PHI. See ``SYNTHETIC_BANNER``.
* Persistence REFUSES to run when ``ENVIRONMENT=production`` (this is a dev/staging
  tool only).
* Ids are derived deterministically from ``--seed`` (uuid5), so re-running is
  idempotent, and persistence uses ``ON CONFLICT DO NOTHING`` (skip-if-exists).

Usage
-----
    python scripts/seed_demo_data.py --dry-run            # generate + print, no writes
    python scripts/seed_demo_data.py --count 5            # generate + persist (staging)
    ENVIRONMENT=staging DATABASE_URL=postgresql://... python scripts/seed_demo_data.py
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import types
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

# --- Make repo packages importable whether run as a script or imported ----------
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# --- Reuse the services' OWN domain models (no ad-hoc dicts) ---------------------
from solace_common.enums import CrisisLevel  # noqa: E402

from services.safety_service.src.domain.entities import (  # noqa: E402
    AssessmentType,
    SafetyAssessment,
)
from services.safety_service.src.domain.value_objects import (  # noqa: E402
    SafetyThresholds,
)
from services.diagnosis_service.src.domain.models import (  # noqa: E402
    SessionState as DiagnosisSessionState,
)
from services.diagnosis_service.src.schemas import DiagnosisPhase  # noqa: E402
from services.therapy_service.src.domain.models import (  # noqa: E402
    SessionState as TherapySessionState,
)
from services.therapy_service.src.schemas import SessionPhase  # noqa: E402
from services.personality_service.src.domain.value_objects import (  # noqa: E402
    OceanScores,
)
from services.memory_service.src.domain.entities import (  # noqa: E402
    ContentType,
    MemoryRecordEntity,
    MemoryTier,
    RetentionCategory,
)


# ---------------------------------------------------------------------------------
# user-service uses a hyphenated (non-importable) package name, so load its
# self-contained domain value-objects + entities via a manufactured package. This
# avoids triggering the heavy domain/__init__ (which pulls DB-backed services).
# ---------------------------------------------------------------------------------
def _load_user_domain() -> types.ModuleType:
    """Load the user-service domain entities module without its package __init__."""
    domain_dir = _REPO_ROOT / "services" / "user-service" / "src" / "domain"
    pkg_name = "_solace_seed_user_domain"
    if pkg_name in sys.modules:
        return sys.modules[pkg_name].entities  # type: ignore[attr-defined]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(domain_dir)]  # type: ignore[attr-defined]
    sys.modules[pkg_name] = pkg

    def _load(sub: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{sub}", domain_dir / f"{sub}.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
        return mod

    _load("value_objects")  # entities.py imports `from .value_objects import ...`
    entities = _load("entities")
    return entities


_user_domain = _load_user_domain()
User = _user_domain.User
UserRole = sys.modules["_solace_seed_user_domain.value_objects"].UserRole
AccountStatus = sys.modules["_solace_seed_user_domain.value_objects"].AccountStatus


SYNTHETIC_BANNER = (
    "=" * 72
    + "\n  SOLACE-AI SYNTHETIC DEMO SEED\n"
    + "  ALL generated data is SYNTHETIC. There is NO real PHI or real user data.\n"
    + "  For dev/staging + end-to-end verification ONLY. Never run in production.\n"
    + "=" * 72
)

DEFAULT_COUNT = 3

# Deterministic namespace so ids are stable per (seed, kind, index) => idempotent.
_SEED_NAMESPACE = uuid5(NAMESPACE_DNS, "solace-ai.synthetic-seed")

# Risk scores chosen so SafetyThresholds maps them to distinct crisis levels.
# Index 0 is ALWAYS CRITICAL, so any dataset (count >= 1) has a crisis case.
_RISK_SCORES: tuple[Decimal, ...] = (
    Decimal("0.95"),  # CRITICAL
    Decimal("0.05"),  # NONE
    Decimal("0.35"),  # LOW
    Decimal("0.55"),  # ELEVATED
    Decimal("0.78"),  # HIGH
)

_CONTENT_BY_LEVEL: dict[str, str] = {
    "CRITICAL": "[SYNTHETIC] I don't want to be here anymore and I have a plan.",
    "HIGH": "[SYNTHETIC] Everything feels hopeless and I can't cope lately.",
    "ELEVATED": "[SYNTHETIC] I've been really anxious and on edge this week.",
    "LOW": "[SYNTHETIC] Work has been a bit stressful but I'm managing.",
    "NONE": "[SYNTHETIC] Just checking in, feeling pretty steady today.",
}


def deterministic_uuid(*parts: Any) -> UUID:
    """Stable uuid5 from the given parts (used so seeding is idempotent)."""
    return uuid5(_SEED_NAMESPACE, ":".join(str(p) for p in parts))


@dataclass
class DemoDataset:
    """Bundle of all synthetic domain records produced by the generator."""

    users: list[Any] = field(default_factory=list)
    safety_assessments: list[SafetyAssessment] = field(default_factory=list)
    diagnosis_sessions: list[DiagnosisSessionState] = field(default_factory=list)
    therapy_sessions: list[TherapySessionState] = field(default_factory=list)
    personality_profiles: list[OceanScores] = field(default_factory=list)
    memory_entries: list[MemoryRecordEntity] = field(default_factory=list)

    def crisis_cases(self) -> list[SafetyAssessment]:
        """Safety assessments classified at the CRITICAL crisis level."""
        return [a for a in self.safety_assessments if a.crisis_level == "CRITICAL"]

    def counts(self) -> dict[str, int]:
        return {
            "users": len(self.users),
            "safety_assessments": len(self.safety_assessments),
            "diagnosis_sessions": len(self.diagnosis_sessions),
            "therapy_sessions": len(self.therapy_sessions),
            "personality_profiles": len(self.personality_profiles),
            "memory_entries": len(self.memory_entries),
            "crisis_cases": len(self.crisis_cases()),
        }


# --- Pure generators (NO database) ----------------------------------------------
def generate_users(count: int, seed: int = 42) -> list[Any]:
    """Build `count` synthetic, activated demo users."""
    if count < 1:
        raise ValueError("count must be >= 1")
    users: list[Any] = []
    for i in range(count):
        users.append(
            User(
                user_id=deterministic_uuid(seed, "user", i),
                email=f"demo.user.{i}.seed{seed}@synthetic.solace.test",
                # NOT a real hash — a synthetic placeholder; these accounts are fake.
                password_hash="synthetic-not-a-real-bcrypt-hash-000000000000",
                display_name=f"Demo User {i}",
                role=UserRole.USER,
                status=AccountStatus.ACTIVE,
                email_verified=True,
            )
        )
    return users


def _is_crisis_index(i: int) -> bool:
    """Index maps to a CRITICAL-risk synthetic case."""
    return i % len(_RISK_SCORES) == 0


def generate_safety_assessments(
    users: list[Any], seed: int = 42
) -> list[SafetyAssessment]:
    """One safety assessment per user, spread across risk levels (index 0 CRITICAL)."""
    thresholds = SafetyThresholds()
    out: list[SafetyAssessment] = []
    for i, user in enumerate(users):
        score = _RISK_SCORES[i % len(_RISK_SCORES)]
        level = thresholds.get_level_for_score(score)
        is_critical = level == "CRITICAL"
        out.append(
            SafetyAssessment(
                assessment_id=deterministic_uuid(seed, "safety", i),
                user_id=user.user_id,
                assessment_type=(
                    AssessmentType.TRIGGERED if is_critical else AssessmentType.PRE_CHECK
                ),
                content_assessed=_CONTENT_BY_LEVEL.get(level, "[SYNTHETIC] check-in"),
                risk_score=score,
                crisis_level=level,
                is_safe=level in ("NONE", "LOW"),
                trigger_indicators=(
                    ["KEYWORD:plan", "PATTERN:hopelessness"] if is_critical else []
                ),
                recommended_action="escalate" if is_critical else "continue",
                requires_escalation=is_critical,
                requires_human_review=level in ("HIGH", "CRITICAL"),
            )
        )
    return out


def generate_diagnosis_sessions(
    users: list[Any], seed: int = 42
) -> list[DiagnosisSessionState]:
    """One diagnosis session per user; crisis users carry a safety flag + CRISIS phase."""
    out: list[DiagnosisSessionState] = []
    for i, user in enumerate(users):
        crisis = _is_crisis_index(i)
        out.append(
            DiagnosisSessionState(
                session_id=deterministic_uuid(seed, "diag", i),
                user_id=user.user_id,
                session_number=1,
                phase=DiagnosisPhase.CRISIS if crisis else DiagnosisPhase.ASSESSMENT,
                safety_flags=["SI_RISK"] if crisis else [],
            )
        )
    return out


def generate_therapy_sessions(
    users: list[Any], seed: int = 42
) -> list[TherapySessionState]:
    """One therapy session per user; crisis users are CRISIS phase at CRITICAL risk."""
    out: list[TherapySessionState] = []
    for i, user in enumerate(users):
        crisis = _is_crisis_index(i)
        out.append(
            TherapySessionState(
                session_id=deterministic_uuid(seed, "therapy", i),
                user_id=user.user_id,
                treatment_plan_id=deterministic_uuid(seed, "treatment_plan", i),
                session_number=1,
                current_phase=SessionPhase.CRISIS if crisis else SessionPhase.WORKING,
                current_risk=CrisisLevel.CRITICAL if crisis else CrisisLevel.LOW,
                safety_flags=["SI_RISK"] if crisis else [],
            )
        )
    return out


def generate_personality_profiles(
    users: list[Any], seed: int = 42
) -> list[OceanScores]:
    """A deterministic Big-Five (OCEAN) profile per user, traits in [0.2, 0.8]."""

    def _trait(i: int, key: str) -> Decimal:
        bucket = int(deterministic_uuid(seed, "trait", i, key)) % 1000
        return Decimal(str(round(0.2 + (bucket / 1000.0) * 0.6, 3)))

    out: list[OceanScores] = []
    for i, _user in enumerate(users):
        out.append(
            OceanScores(
                openness=_trait(i, "O"),
                conscientiousness=_trait(i, "C"),
                extraversion=_trait(i, "E"),
                agreeableness=_trait(i, "A"),
                neuroticism=_trait(i, "N"),
                overall_confidence=Decimal("0.6"),
            )
        )
    return out


def generate_memory_entries(
    users: list[Any], seed: int = 42
) -> list[MemoryRecordEntity]:
    """Episodic session-summary memories, plus a safety-critical crisis memory."""
    out: list[MemoryRecordEntity] = []
    for i, user in enumerate(users):
        out.append(
            MemoryRecordEntity(
                record_id=deterministic_uuid(seed, "mem", i, "summary"),
                user_id=user.user_id,
                content=f"[SYNTHETIC] Session summary for demo user {i}: discussed "
                "coping skills and mood tracking.",
                content_type=ContentType.SESSION_SUMMARY,
                tier=MemoryTier.TIER_4_EPISODIC,
                retention_category=RetentionCategory.LONG_TERM,
            )
        )
        if _is_crisis_index(i):
            out.append(
                MemoryRecordEntity(
                    record_id=deterministic_uuid(seed, "mem", i, "crisis"),
                    user_id=user.user_id,
                    content="[SYNTHETIC] Crisis event flagged during session; "
                    "safety plan reviewed.",
                    content_type=ContentType.CRISIS_EVENT,
                    tier=MemoryTier.TIER_4_EPISODIC,
                    retention_category=RetentionCategory.PERMANENT,
                    is_safety_critical=True,
                )
            )
    return out


def generate_demo_dataset(count: int = DEFAULT_COUNT, seed: int = 42) -> DemoDataset:
    """Build the full synthetic dataset. Pure — requires no database."""
    users = generate_users(count, seed=seed)
    return DemoDataset(
        users=users,
        safety_assessments=generate_safety_assessments(users, seed=seed),
        diagnosis_sessions=generate_diagnosis_sessions(users, seed=seed),
        therapy_sessions=generate_therapy_sessions(users, seed=seed),
        personality_profiles=generate_personality_profiles(users, seed=seed),
        memory_entries=generate_memory_entries(users, seed=seed),
    )


# --- Environment / persistence --------------------------------------------------
def is_production() -> bool:
    """True when the runtime environment is production (persistence forbidden)."""
    return os.getenv("ENVIRONMENT", "").strip().lower() in {"production", "prod"}


async def _persist_async(dataset: DemoDataset, database_url: str) -> dict[str, int]:
    """Idempotently persist a compact JSON view of the dataset (skip-if-exists).

    Writes to a dedicated ``demo_seed_records`` table (created if missing) rather
    than guessing every service's schema. This keeps the seed safe and idempotent
    on any staging Postgres while still exercising the real ``DATABASE_URL``.
    """
    import json

    try:
        import asyncpg  # type: ignore
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "asyncpg is required for persistence; install it or use --dry-run."
        ) from exc

    # asyncpg speaks the bare postgres DSN, not SQLAlchemy's +asyncpg dialect.
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    written = 0
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_seed_records (
                record_id UUID PRIMARY KEY,
                kind TEXT NOT NULL,
                user_id UUID,
                payload JSONB NOT NULL,
                is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        rows: list[tuple[UUID, str, UUID | None, str]] = []
        for u in dataset.users:
            rows.append((u.user_id, "user", u.user_id, json.dumps({"email": u.email})))
        for a in dataset.safety_assessments:
            rows.append(
                (
                    a.assessment_id,
                    "safety_assessment",
                    a.user_id,
                    json.dumps({"crisis_level": a.crisis_level}),
                )
            )
        for s in dataset.diagnosis_sessions:
            rows.append(
                (s.session_id, "diagnosis_session", s.user_id, json.dumps({}))
            )
        for s in dataset.therapy_sessions:
            rows.append((s.session_id, "therapy_session", s.user_id, json.dumps({})))
        for m in dataset.memory_entries:
            rows.append(
                (
                    m.record_id,
                    "memory_entry",
                    m.user_id,
                    json.dumps({"content_type": m.content_type.value}),
                )
            )
        for record_id, kind, user_id, payload in rows:
            status = await conn.execute(
                """
                INSERT INTO demo_seed_records (record_id, kind, user_id, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (record_id) DO NOTHING
                """,
                record_id,
                kind,
                user_id,
                payload,
            )
            if status.endswith("1"):
                written += 1
    finally:
        await conn.close()
    return {"inserted": written, "total": len(rows)}


def persist_dataset(
    dataset: DemoDataset, database_url: str | None = None
) -> dict[str, int]:
    """Persist the dataset to ``DATABASE_URL``. Refuses to run in production."""
    if is_production():
        raise RuntimeError(
            "Refusing to persist synthetic seed data while ENVIRONMENT=production. "
            "This tool is for dev/staging only."
        )
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "No DATABASE_URL provided; cannot persist. Use --dry-run to generate only."
        )
    import asyncio

    return asyncio.run(_persist_async(dataset, url))


def _print_summary(dataset: DemoDataset) -> None:
    counts = dataset.counts()
    print("\nGenerated synthetic records:")
    for key, value in counts.items():
        print(f"  {key:<22} {value}")
    crisis = dataset.crisis_cases()
    if crisis:
        print(
            f"\n  CRITICAL crisis case(s): {len(crisis)} "
            f"(e.g. user {crisis[0].user_id})"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate (and optionally persist) SYNTHETIC Solace-AI demo data."
    )
    parser.add_argument(
        "--count", type=int, default=DEFAULT_COUNT, help="Number of demo users."
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Deterministic seed (idempotent ids)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and print records only; perform NO database writes.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL for persistence.",
    )
    args = parser.parse_args(argv)

    print(SYNTHETIC_BANNER)
    dataset = generate_demo_dataset(count=args.count, seed=args.seed)
    _print_summary(dataset)

    if args.dry_run:
        print("\n[dry-run] No database writes performed.")
        return 0

    if is_production():
        print(
            "\nERROR: Refusing to persist in production. Re-run with --dry-run.",
            file=sys.stderr,
        )
        return 2

    try:
        result = persist_dataset(dataset, database_url=args.database_url)
    except Exception as exc:  # noqa: BLE001 - surface any persistence failure clearly
        print(f"\nERROR: persistence failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nPersisted synthetic demo data: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
