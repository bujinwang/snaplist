"""`apps/api/devtools.py` must be unreachable outside a local environment.

Why this file exists and why it is not paranoia
----------------------------------------------
`devtools attach-asset` writes an `assets` row without running the vision pipeline. In production
that is precisely the lie the export endpoint exists to avoid: an item would look "prettified"
with no prettify job behind it, and a seller would download a CSV pointing at an image nobody
generated. The guard is one `if` in `_guard()`, verified here so that deleting it fails a test
instead of shipping quietly.

The settings object is injected by monkeypatching `get_settings` — a test double for a config
reader, not a fake of the pipeline. Nothing is written to a database: every case below either
exits at the guard or is asserted not to have raised.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from apps.api import devtools
from apps.api.settings import Settings

ITEM = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"

ARGV = [
    "attach-asset",
    "--item",
    ITEM,
    "--user",
    USER,
    "--kind",
    "prettified",
    "--path",
    "https://example.test/p.png",
]


def _settings(env: str) -> Settings:
    return Settings(env=env, supabase_jwt_secret="")


@pytest.mark.parametrize("env", ["production", "staging", "PRODUCTION", "prod"])
def test_the_cli_refuses_outside_local_environments(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    """Any non-local ENV exits non-zero *before* touching the database."""
    monkeypatch.setattr(devtools, "get_settings", lambda: _settings(env))
    with pytest.raises(SystemExit) as excinfo:
        devtools.main(ARGV)
    message = str(excinfo.value)
    assert "refusing to run" in message, message
    assert env in message, "the error must name the environment it refused, not just refuse"
    assert excinfo.value.code not in (0, None), "SystemExit must carry a failure code"


@pytest.mark.parametrize("env", ["local", "test", "development", "LOCAL"])
def test_the_guard_passes_in_local_environments(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    """`_guard()` is a permission check, not a pipeline: locally it returns None, silently."""
    monkeypatch.setattr(devtools, "get_settings", lambda: _settings(env))
    assert devtools._guard() is None


def test_the_guard_runs_before_any_repository_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering matters: a guard that runs after the write would still mutate production.

    `build_repository` is replaced with a function that records being called, so this asserts
    the refusal happens without ever opening a database.
    """
    monkeypatch.setattr(devtools, "get_settings", lambda: _settings("production"))

    calls: list[str] = []

    def exploding_build_repository(*args: object, **kwargs: object) -> None:
        calls.append("build_repository")
        raise AssertionError("the database must not be opened outside local environments")

    monkeypatch.setattr(devtools, "build_repository", exploding_build_repository)

    with pytest.raises(SystemExit):
        devtools.main(ARGV)
    assert calls == [], "the ENV guard must run before build_repository"


def test_attach_asset_refuses_an_item_owned_by_somebody_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even locally the CLI is scoped to the owner uuid, not to the item id alone.

    This mirrors the cross-user isolation rule the API enforces: a wrong `--user` must not let a
    human attach an asset to a stranger's item.
    """

    class NotMine:
        def get_item(self, *args: object, **kwargs: object) -> None:
            return None

    monkeypatch.setattr(devtools, "get_settings", lambda: _settings("local"))
    monkeypatch.setattr(devtools, "build_repository", lambda *a, **k: NotMine())

    from argparse import Namespace

    args = Namespace(db=None, item=ITEM, user=USER, kind="prettified", path="p", position=0)
    assert devtools.attach_asset(args) == 2, "a foreign item must exit 2, not silently succeed"
    # sanity: the uuids in the fixture are well-formed, so the 2 above is about ownership
    UUID(ITEM)
    UUID(USER)
