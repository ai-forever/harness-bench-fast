"""Independent public-client witnesses for the v8 systems contract review."""

from __future__ import annotations

import copy
import json
from collections import deque
from contextlib import contextmanager
from itertools import product

import pytest

from harness_bench.feasibility_tasks.trusted_service import get_session, trusted_service_available
from harness_bench.general_hard import interactive_systems as systems

pytestmark = pytest.mark.skipif(
    not trusted_service_available(), reason="Unix socket transport unavailable",
)


@contextmanager
def scenario(number, workspace, variant=0):
    task = systems.build_task(number, variant=variant)
    task.setup(workspace)
    try:
        yield task
    finally:
        task.cleanup(workspace)


def command(workspace, verb, **options):
    arguments = [verb]
    for key, value in options.items():
        arguments.extend(("--" + key.replace("_", "-"), str(value)))
    return systems.send(workspace, *arguments)


def submit(workspace, verb, data):
    request = workspace / "review-request.json"
    request.write_text(json.dumps(data), encoding="utf-8")
    return command(workspace, verb, file=request.name)


@pytest.mark.parametrize("number", (492, 493))
def test_completed_release_cannot_acquire_new_staging(number, tmp_path):
    with scenario(number, tmp_path) as task:
        task.apply_gold(tmp_path)
        assert task.verify(tmp_path).passed
        current = command(tmp_path, "status")
        package = next(iter(current["packages"]))
        row = current["packages"][package]
        response = submit(tmp_path, "stage", {"rows": [{
            "package": package, "version": row["version"],
            "revision": row["revision"],
            "digest": current["artifacts"][package][str(row["version"])],
        }]})
        assert response["status"] == "closed"
        assert command(tmp_path, "status")["staged"] == {}
        assert task.verify(tmp_path).passed


@pytest.mark.parametrize("number", (492, 493))
def test_release_verifier_rechecks_empty_staging(number, tmp_path):
    with scenario(number, tmp_path) as task:
        task.apply_gold(tmp_path)
        state = copy.deepcopy(get_session(tmp_path).state)
        assert systems.check_goal(number, 0, state) is None
        state["staged"]["base"] = {"receipt": "unconsumed-source"}
        assert systems.check_goal(number, 0, state) is not None


def exhaustive_release_path(observed, target, allow_bridges):
    """Reachability from the public finite compatibility relation, not gold code."""
    names = tuple(observed["packages"])
    choices = [tuple(int(version) for version in observed["costs"][name]
                     if allow_bridges or int(version) != 3) for name in names]
    # Include the initial world even if it already contains a bridge version.
    start = tuple(observed["packages"][name]["version"] for name in names)
    candidates = set(product(*choices)) | {start}
    positions = {name: index for index, name in enumerate(names)}
    valid = {state for state in candidates if all(
        [state[positions[rule["left"]]], state[positions[rule["right"]]]]
        in rule["allowed"] for rule in observed["contracts"]
    )}
    queue = deque([start])
    previous = {start: None}
    while queue:
        current = queue.popleft()
        if current == (target,) * len(names):
            path = []
            while previous[current] is not None:
                before = previous[current]
                path.append({name: current[index] for index, name in enumerate(names)
                             if current[index] != before[index]})
                current = before
            return list(reversed(path))
        for following in sorted(valid):
            if following in previous:
                continue
            changed = [index for index in range(len(names)) if following[index] != current[index]]
            if not allow_bridges and any(following[index] == 3 for index in changed):
                continue
            cost = sum(observed["costs"][names[index]][str(following[index])]
                       for index in changed)
            if cost <= observed["capacity"]:
                previous[following] = current
                queue.append(following)
    return None


@pytest.mark.parametrize("number", (492, 493))
@pytest.mark.parametrize("variant", (0, 1))
def test_release_has_public_solution_and_requires_intermediate_version(number, variant, tmp_path):
    with scenario(number, tmp_path, variant) as task:
        current = command(tmp_path, "status")
        target = json.loads((tmp_path / "manifest.json").read_text())["version"]
        assert exhaustive_release_path(current, target, allow_bridges=False) is None
        path = exhaustive_release_path(current, target, allow_bridges=True)
        assert path
        for cohort in path:
            current = command(tmp_path, "status")
            rows = [{"package": name, "version": version,
                     "revision": current["packages"][name]["revision"],
                     "digest": current["artifacts"][name][str(version)]}
                    for name, version in cohort.items()]
            assert submit(tmp_path, "stage", {"rows": rows})["status"] == "outcome_unknown"
            durable = command(tmp_path, "status")["staged"]
            assert submit(tmp_path, "activate", {"receipts": [durable[name]["receipt"]
                                                               for name in cohort]})["status"] == "outcome_unknown"
        assert command(tmp_path, "finish")["status"] == "finished"
        assert task.verify(tmp_path).passed


@pytest.mark.parametrize("number", (496, 497))
def test_completed_transfer_does_not_allow_erasing_needed_original(number, tmp_path):
    with scenario(number, tmp_path) as task:
        pins = {}
        for name in ("d0", "d1"):
            current = command(tmp_path, "open", id=name)
            pins[name] = command(tmp_path, "pin", id=name, revision=current["revision"])
        current = command(tmp_path, "open", id="d0")
        data = {"id": "d0", "revision": current["revision"],
                "pins": {name: pin["receipt"] for name, pin in pins.items()}}
        if number == 496:
            document = current["document"]
            document["heading"] = "release-d0"
            document["tags"].append("shipped")
            document["payload"] = pins["d1"]["values"]
            data["document"] = document
            response = submit(tmp_path, "save", data)
        else:
            data["edits"] = [{"line": anchor, "old": old, "new": pins["d1"]["values"][field]}
                             for anchor, old, field in (("target", "left-0", "left"),
                                                        ("other", "right-0", "right"))]
            response = submit(tmp_path, "patch", data)
        assert response["status"] == "saved"
        blocked = command(tmp_path, "unpin", id="d0")
        assert blocked["status"] == "original_still_needed"
        assert blocked["consumers"] == ["d5"]
        current = command(tmp_path, "open", id="d2")
        assert command(tmp_path, "pin", id="d2", revision=current["revision"])["status"] == "pin_capacity"
        assert not task.verify(tmp_path).passed
        assert get_session(tmp_path).replay().state["violations"] == []
