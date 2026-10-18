"""Ensure host-owned feasibility services cannot leak between test cases."""

import pytest


@pytest.fixture(autouse=True)
def close_feasibility_sessions_after_test():
    yield
    from harness_bench.feasibility_tasks.trusted_service import close_all_sessions

    close_all_sessions()
