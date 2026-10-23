"""The new graders reject malformed submissions before comparing content."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness_bench.general_hard.common import artifact_task, program_task, same_json


@pytest.fixture
def artifact():
    return artifact_task(
        number=472, slug="test", name="test", parent=352, prompt="test",
        files={"input/data.json": '{"n":1}\n'}, expected={"count": 1, "items": [1, 2]},
    )


def test_type_and_key_strictness():
    assert not same_json({"n": True}, {"n": 1})
    assert not same_json({"n": 1.0}, {"n": 1})
    assert not same_json({"n": 1, "extra": None}, {"n": 1})
    assert not same_json([1, 2], [2, 1])
    assert same_json({"b": [], "a": 1}, {"a": 1, "b": []})


def test_artifact_lifecycle(artifact, tmp_path):
    artifact.setup(tmp_path)
    assert not artifact.verify(tmp_path).passed
    artifact.apply_gold(tmp_path)
    assert artifact.verify(tmp_path).passed
    (tmp_path / "input/data.json").write_text('{"n":2}\n')
    assert not artifact.verify(tmp_path).passed


@pytest.mark.parametrize("text", [
    '{"count":1,"count":1,"items":[1,2]}',
    '{"count":true,"items":[1,2]}',
    '{"count":1,"items":[2,1]}',
    '{"count":1,"items":[1,2],"extra":0}',
    '{"count":NaN,"items":[1,2]}',
    '{"count":1.0,"items":[1,2]}',
])
def test_artifact_bad_json_is_rejected(artifact, tmp_path, text):
    artifact.setup(tmp_path)
    (tmp_path / "answer.json").write_text(text)
    assert not artifact.verify(tmp_path).passed


def test_artifact_symlink_is_rejected(artifact, tmp_path):
    artifact.setup(tmp_path)
    artifact.apply_gold(tmp_path)
    answer = tmp_path / "answer.json"
    target = tmp_path / "copy.json"
    answer.rename(target)
    try:
        answer.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert not artifact.verify(tmp_path).passed


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX only")
def test_artifact_fifo_is_rejected_without_blocking(artifact, tmp_path):
    artifact.setup(tmp_path)
    os.mkfifo(tmp_path / "answer.json")
    assert not artifact.verify(tmp_path).passed


def test_program_process_contract(tmp_path):
    task = program_task(
        number=472, slug="test_program", name="test", parent=352, prompt="echo",
        cases=[({"n": n}, {"n": n}) for n in range(4)] + [
            ("paragraph\u2028separator\u2029next\u0085line", "paragraph\u2028separator\u2029next\u0085line"),
        ],
        gold="import sys\nfor line in sys.stdin:\n print(line.strip())\n",
    )
    task.setup(tmp_path)
    assert not task.verify(tmp_path).passed
    task.apply_gold(tmp_path)
    assert task.verify(tmp_path).passed
    (tmp_path / "solution.py").write_text("print('{}')\n")
    assert not task.verify(tmp_path).passed


def test_program_cannot_read_workspace_neighbors(tmp_path):
    task = program_task(
        number=472, slug="test_copy", name="test", parent=352, prompt="echo",
        cases=[({}, False)],
        gold=(
            "import json, pathlib, sys\n"
            "for line in sys.stdin:\n"
            " print(json.dumps(pathlib.Path('private-answer.txt').exists()))\n"
        ),
    )
    task.setup(tmp_path)
    (tmp_path / "private-answer.txt").write_text("secret grading state")
    task.apply_gold(tmp_path)
    assert task.verify(tmp_path).passed


def test_examples_have_only_two_cases(tmp_path):
    task = program_task(
        number=472, slug="test_examples", name="test", parent=352, prompt="echo",
        cases=[(n, n) for n in range(12)], gold="",
    )
    task.setup(tmp_path)
    assert len(json.loads((Path(tmp_path) / "examples.json").read_text())) == 2
