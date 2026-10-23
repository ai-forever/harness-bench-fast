"""Twenty bidirectionally coupled workflows, over one trusted host session.

Only transport and public contracts enter the agent workspace. Each existing
workflow keeps its own command clock, namespace and durable history. Coupling
modules implement the documented, bounded exchanges of real domain data.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from harness_bench.core import Task, VerifyResult
from harness_bench.reflect_tasks.common import Service

from .interactive_common import interactive_task, workflow_definition

MAX_CALLS = 320
_LINK_MODULES = ("composite_links_a", "composite_links_b", "composite_links_c")


def get_pair(number: int) -> dict[str, Any]:
    for name in _LINK_MODULES:
        module = importlib.import_module(f"harness_bench.general_hard.{name}")
        if number in module.PAIRS:
            return module.PAIRS[number]
    raise KeyError(f"unknown composite task: {number}")


def original_tasks() -> dict[int, Task]:
    from .interactive_data import TASKS as data
    from .interactive_systems import TASKS as systems
    from .interactive_workflows import TASKS as workflows

    return {int(task.id.split("_")[1]): task for task in [*data, *systems, *workflows]}


class _LaneIO:
    def __init__(self, io, lane):
        self.io = io
        self.lane = lane

    def read(self, relative):
        path = PurePosixPath(str(relative))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise FileNotFoundError(str(relative))
        return self.io.read(str(PurePosixPath(self.lane) / path))


class PairRuntime:
    """A new instance is built for execution and separately for trusted replay."""

    def __init__(self, number):
        self.pair = get_pair(number)
        tasks = original_tasks()
        self.definitions = {
            lane: workflow_definition(tasks[self.pair[lane]]) for lane in ("left", "right")
        }
        self.namespaces = {
            lane: Service("taskctl", lane, definition.source, b"").namespace()
            for lane, definition in self.definitions.items()
        }

    def initial_state(self):
        state = {
            lane: {**namespace["initial_state"](), "clock": 0}
            for lane, namespace in self.namespaces.items()
        }
        state.update(clock=0, coupling={"phase": 0, "events": []})
        if "initial" in self.pair:
            self.pair["initial"](state)
        return state

    def lane_check(self, state, lane):
        check = self.pair.get("check_" + lane, self.definitions[lane].check)
        return check(state[lane])

    def check(self, state):
        for lane in ("left", "right"):
            reason = self.lane_check(state, lane)
            if reason is not None:
                return f"{lane}: {reason}"
        return self.pair["check"](state)

    def handle(self, state, words, opts, io):
        if words in (["status"], ["pair", "status"]):
            return {
                "status": "ok", "coupling": copy.deepcopy(state["coupling"]),
                "left_done": self.lane_check(state, "left") is None,
                "right_done": self.lane_check(state, "right") is None,
                "joint_done": self.check(state) is None,
                "service_calls": state["clock"], "service_limit": MAX_CALLS,
            }, 0
        if words in (["help"], ["pair", "help"]):
            return {"status": "ok", "documentation": "docs/taskctl.md"}, 0
        if not words or words[0] not in self.namespaces:
            return {"status": "invalid_command", "commands": ["left ...", "right ...", "pair status"]}, 2
        lane = words[0]
        child = state[lane]
        child["clock"] = child.get("clock", 0) + 1
        before = copy.deepcopy(child)
        event_count = len(state["coupling"]["events"])
        try:
            response, code = self.namespaces[lane]["handle"](
                child, words[1:], opts, _LaneIO(io, lane),
            )
        except (KeyError, IndexError, TypeError, ValueError, OverflowError, FileNotFoundError):
            child.clear()
            child.update(before)
            response, code = {"status": "invalid_arguments", "documentation": f"{lane}/docs/taskctl.md"}, 2
        # A nonzero reply may have durable effects. Coupling observes committed
        # domain state, not the exit code; all exchanges are replayed as well.
        response = copy.deepcopy(response)
        self.pair["after"](state, lane, words[1:], opts, response, code)
        if len(state["coupling"]["events"]) > event_count:
            response["coupling_events"] = copy.deepcopy(state["coupling"]["events"][event_count:])
        return response, code


def _lane_client(lane: str) -> str:
    return f'''#!/usr/bin/env python3
"""Public scoped transport. File arguments are relative to this lane directory."""
import json
import socket
import sys
from pathlib import Path

try:
    root = Path(__file__).resolve().parents[2]
    connection = json.loads((root / ".svc/connection.json").read_text(encoding="utf-8"))
    request = json.dumps({{"argv": [{lane!r}, *sys.argv[1:]]}}, ensure_ascii=False).encode() + b"\\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect(connection["socket"])
        sock.sendall(request)
        with sock.makefile("rb") as stream:
            result = json.loads(stream.readline(2000001))
    print(json.dumps(result["response"], ensure_ascii=False, indent=2))
    sys.exit(result["code"])
except (OSError, ValueError, KeyError) as exc:
    print(json.dumps({{"status": "error", "message": "task service unavailable: " + str(exc)}}))
    sys.exit(2)
'''


_DOCS = """# Coupled workflow service

Solve both operational goals and the bounded cross-service obligations below.
The two lanes are one task, with one shared budget and one trusted history.
Both final domain goals must hold simultaneously after all cross-service
events. Independent solutions can become obsolete when the other lane acts.

Commands from the task root:
  python3 tools/taskctl left COMMAND [OPTIONS]
  python3 tools/taskctl right COMMAND [OPTIONS]
  python3 tools/taskctl pair status

Equivalent scoped clients are left/tools/taskctl and right/tools/taskctl.
Read left/TASK.md, right/TASK.md and each lane's docs/taskctl.md. Each lane's
fixture files keep their documented names inside that directory. A --file
argument passed to a lane is RELATIVE TO THAT LANE DIRECTORY, regardless of
the shell's current directory. Absolute paths and '..' are rejected. Own
scripts and submissions may be created in either lane; fixed inputs may not
be changed. There is one connection file at the task root.

The shared limit is 320 service requests, including reads, errors and pair
status. There is no additional 160-call per-lane limit. A lane's logical clock
advances only when that lane receives a command; reading the peer or pair
status does not expire its local leases. Cross-service changes are caused by
the explicit finite rules below, never real time. A nonzero exit code can
still mean a durable domain effect, as documented by the lane. Such effects
also trigger cross-service events. New events are included in the response's
coupling_events and retained by pair status. Do not discard them when handling
an uncertain acknowledgement. Read-only commands do not themselves create
new cross-service obligations once the triggering event has been delivered.

Pair status reports observable event data and semantic readiness; a lane's
ready flag alone is not joint completion. No extra commit or secret command
is required: both current goals and all documented exchanges must be complete.
You may batch public-client requests in scripts and process their responses.

## Cross-service rules for this pair

"""


def _dependency_digest() -> str:
    """Bind imported host callbacks as well as the literal service wrapper."""
    root = Path(__file__).parent
    files = [root / "composition.py", root / "interactive_common.py"]
    files += [root / f"{name}.py" for name in _LINK_MODULES]
    files += [root / f"interactive_{name}.py" for name in ("data", "systems", "workflows")]
    payload = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def make_task(number: int) -> Task:
    pair = get_pair(number)
    originals = original_tasks()
    files = {}
    protocols = {}
    parents = set()
    for lane in ("left", "right"):
        task = originals[pair[lane]]
        definition = workflow_definition(task)
        protocols[lane] = task.runtime_protocol
        parents.update(tag for tag in task.tags if tag.startswith("parent-"))
        files[f"{lane}/tools/taskctl"] = _lane_client(lane)
        files[f"{lane}/docs/taskctl.md"] = definition.docs + (
            "\nThe composite contract in ../../docs/taskctl.md adds cross-service "
            "updates and overrides any previous per-lane call budget.\n"
        )
        files[f"{lane}/TASK.md"] = task.prompt.split("\n\nЭто интерактивная задача.")[0] + "\n"
        files.update({f"{lane}/{name}": text for name, text in definition.files.items()})
    if pair["left"] == 473 or pair["right"] == 473:
        lane = "left" if pair["left"] == 473 else "right"
        files[f"{lane}/docs/taskctl.md"] += (
            "\nWindow key encoding: the interval [10*k,10*k+10) has JSON key "
            "str(10*k), the decimal window START, not str(k). For example the "
            "window [10,20) is key \"10\".\n"
        )
    source = (
        f"# Host dependency digest: {_dependency_digest()}\n"
        f"# Original contracts: {json.dumps(protocols, sort_keys=True)}\n"
        "from harness_bench.general_hard.composition import PairRuntime\n"
        f"_pair_runtime = PairRuntime({number})\n"
        "initial_state = _pair_runtime.initial_state\n"
        "handle = _pair_runtime.handle\n"
    )
    runtime = PairRuntime(number)
    base = Task(
        id=f"task_{number}_{pair['slug']}", name=pair["title"], prompt="",
        verifier=lambda _: VerifyResult(False), tags=tuple(sorted(parents)),
    )
    return interactive_task(
        base, prompt=(
            f"Составная задача {number}: {pair['title']}. Реши обе связанные части "
            f"(исходные задачи {pair['left']} и {pair['right']}) и согласуй все "
            "встречные изменения. Текущие результаты обеих частей должны быть "
            "верны одновременно. Правила связей — docs/taskctl.md; цели и данные "
            "частей — left/TASK.md и right/TASK.md. Однократного независимого "
            "выполнения каждой половины недостаточно. В калибровке DeepAgents "
            "доступно 160 шагов графа (не 160 сервисных запросов); используй скрипты "
            "для серий запросов и оставь шаги на завершение. Эффективные лимиты "
            "другого харнесса сообщаются в конфигурации запуска."
        ),
        docs=_DOCS + pair["docs"], source=source, check=runtime.check,
        gold=pair["gold"], files=files, max_calls=MAX_CALLS,
        revision="wave21-composite-v1",
        suite_tags=("composite-hard", "wave21", "interactive", "composite-v1"),
        tags=(f"source-{pair['left']}", f"source-{pair['right']}"),
    )
