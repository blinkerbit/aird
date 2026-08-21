"""socketify is single-process; worker helpers always return 1."""

from aird.server_runtime import describe_worker_layout, resolve_worker_count


def test_resolve_worker_count_always_one():
    assert resolve_worker_count(None) == 1
    assert resolve_worker_count(8) == 1
    assert resolve_worker_count(1) == 1


def test_describe_worker_layout():
    text = describe_worker_layout(1)
    assert "workers=1" in text
    assert "socketify" in text
