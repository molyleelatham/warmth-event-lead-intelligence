"""Demo-user configuration and the seeded GTM roster."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from warmth.apps.api.store import DemoStore
from warmth.apps.lifecycle import user_bootstrap
from packages.core.demo_user import demo_user_email

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_demo_user_email_reads_env(monkeypatch):
    monkeypatch.setenv("WARMTH_DEMO_USER_EMAIL", " Owner@Example.com ")
    assert demo_user_email() == "owner@example.com"


def test_demo_user_email_blank_when_unset(monkeypatch):
    monkeypatch.delenv("WARMTH_DEMO_USER_EMAIL", raising=False)
    assert demo_user_email() == ""
    monkeypatch.setenv("WARMTH_DEMO_USER_EMAIL", "   ")
    assert demo_user_email() == ""


def test_maybe_seed_only_for_configured_email(monkeypatch):
    monkeypatch.setenv("WARMTH_DEMO_USER_EMAIL", "owner@example.com")
    seeded: list[str] = []

    class _Store:
        def ensure_user_seed(self, uid: str) -> None:
            seeded.append(uid)

    monkeypatch.setattr(user_bootstrap, "get_store", lambda uid: _Store())
    user_bootstrap.maybe_seed_owner_demo("uid-a", "Owner@Example.com")
    user_bootstrap.maybe_seed_owner_demo("uid-b", "someone-else@example.com")
    assert seeded == ["uid-a"]


def test_maybe_seed_does_nothing_when_unset(monkeypatch):
    monkeypatch.delenv("WARMTH_DEMO_USER_EMAIL", raising=False)

    def _boom(uid: str):
        raise AssertionError(f"should not seed {uid}")

    monkeypatch.setattr(user_bootstrap, "get_store", _boom)
    user_bootstrap.maybe_seed_owner_demo("uid-a", "owner@example.com")


async def test_bootstrap_seeds_only_the_configured_account(monkeypatch):
    monkeypatch.setenv("WARMTH_DEMO_USER_EMAIL", "owner@example.com")
    seeded: list[str] = []

    class _Store:
        def ensure_user_seed(self, uid: str) -> None:
            seeded.append(uid)

    class _Record:
        def __init__(self, email: str) -> None:
            self.email = email
            self.display_name = "Owner"
            self.photo_url = None

    records = {
        "uid-a": _Record("owner@example.com"),
        "uid-b": _Record("someone-else@example.com"),
    }
    monkeypatch.setattr(user_bootstrap, "get_store", lambda uid: _Store())
    monkeypatch.setattr(user_bootstrap.firebase_auth, "get_user", lambda uid: records[uid])

    service = user_bootstrap.UserBootstrapService(firestore=None)
    owner = await service.bootstrap("uid-a")
    other = await service.bootstrap("uid-b")

    assert owner.demo_seeded is True
    assert other.demo_seeded is False
    assert seeded == ["uid-a"]


def test_seeded_roster_keeps_attendee_without_an_email():
    store = DemoStore()
    zamir = next(c for c in store.pre_connections.values() if c.name == "Zamir")
    assert not (zamir.email or "").strip()
    assert zamir.id == "premeet_zamir"
    assert zamir.icp_score == 65
    assert zamir.predicted_warmth == 50


def test_migrate_script_exits_without_demo_email():
    env = os.environ.copy()
    env.pop("WARMTH_DEMO_USER_EMAIL", None)
    env.pop("GCP_PROJECT_ID", None)
    env.pop("GOOGLE_CLOUD_PROJECT", None)
    env.pop("GCLOUD_PROJECT", None)
    env["DISABLE_SECRET_MANAGER"] = "true"
    result = subprocess.run(
        [sys.executable, "scripts/migrate_user_demo_to_firestore.py"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "WARMTH_DEMO_USER_EMAIL" in (result.stderr + result.stdout)
