"""
Shared pytest fixtures and options.
"""
import os
import tempfile

import pytest

# Point the DB paths at writable, isolated temp files for the whole test
# session, BEFORE any test module imports xteink_service.koreader_sync. That
# module builds a module-level ProgressStore that captures KOREADER_DB at import
# time, so the first import wins and per-test env overrides can't rebind the
# singleton afterwards. Production always sets these via docker-compose; the
# in-code defaults are the production paths (/data/state/*), which aren't
# writable on a dev/CI host.
os.environ.setdefault("KOREADER_DB", tempfile.mktemp(prefix="xteink-test-koreader-", suffix=".db"))
os.environ.setdefault("STATE_DB", tempfile.mktemp(prefix="xteink-test-state-", suffix=".db"))


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run full-pipeline tests against the real X4 device (must be in File Transfer mode)",
    )


@pytest.fixture(scope="session")
def live(request):
    return request.config.getoption("--live")
