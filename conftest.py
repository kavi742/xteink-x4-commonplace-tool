"""
Shared pytest fixtures and options.
"""
import pytest


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
