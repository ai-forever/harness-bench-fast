import importlib.util
from pathlib import Path


def load(tmp_path, monkeypatch):
    monkeypatch.setenv("HBF_AUDIT_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "audit_proxy", Path(__file__).parents[1] / "scripts/hbf_reasoning_audit_proxy.py"
    )
    p = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p)
    return p


def test_repair_exact_identity_preserves_history_and_detects_loss(tmp_path, monkeypatch):
    p = load(tmp_path, monkeypatch)
    call = {"id": "c1", "function": {"name": "Bash", "arguments": '{"cmd":"pwd"}'}}
    p.observe("brain", {"reasoning_content": "exact\nthought", "tool_calls": [call]})
    body = {"messages": [{"role": "assistant", "content": None, "tool_calls": [call]}]}
    check = p.prepare(body, "brain", False)[0]
    assert not check["sent_exact"] and "reasoning_content" not in body["messages"][0]
    check = p.prepare(body, "brain", True)[0]
    assert check["restored"] and body["messages"][0]["reasoning_content"] == "exact\nthought"
    assert p.prepare(body, "brain", True)[0]["native_exact"]


def test_no_cross_harness_or_tool_signature_injection(tmp_path, monkeypatch):
    p = load(tmp_path, monkeypatch)
    call = {"id": "c1", "function": {"name": "Bash", "arguments": "{}"}}
    p.observe("brain", {"reasoning_content": "private-thought", "tool_calls": [call]})
    body = {"messages": [{"role": "assistant", "tool_calls": [call]}]}
    assert p.prepare(body, "other", True) == []
    body["messages"][0]["tool_calls"][0]["function"]["name"] = "Different"
    assert p.prepare(body, "brain", True) == []
    assert "reasoning_content" not in body["messages"][0]


def test_sqlite_connections_closed_across_many_requests(tmp_path, monkeypatch):
    p = load(tmp_path, monkeypatch)
    connections = []
    real = p.sqlite3.connect

    class Tracked:
        def __init__(self, c):
            self.c = c
            self.closed = False

        def execute(self, *a, **k):
            return self.c.execute(*a, **k)

        def __enter__(self):
            self.c.__enter__()
            return self

        def __exit__(self, *a):
            return self.c.__exit__(*a)

        def close(self):
            self.closed = True
            self.c.close()

    def tracked(*a, **k):
        c = Tracked(real(*a, **k))
        connections.append(c)
        return c

    monkeypatch.setattr(p.sqlite3, "connect", tracked)
    for i in range(100):
        p.observe(
            "test",
            {
                "reasoning_content": "thinking",
                "tool_calls": [{"id": str(i), "function": {"name": "Bash", "arguments": "{}"}}],
            },
        )
        p.prepare({"messages": []}, "test", False)
    assert len(connections) == 200 and all(c.closed for c in connections)
