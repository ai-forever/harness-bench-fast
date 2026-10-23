"""Independent deterministic-world enumeration for shared robust policies."""
import copy
import inspect
import itertools
import json
import random
import textwrap

from harness_bench.general_hard import systems_v6 as s


def code(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_hand_anchors_and_separate_model_policy_is_invalid():
    assert s.solve487(s.TINY) == s.EXPECTED
    assert s.solve487(s.IMPOSSIBLE) == {"feasible": False}
    # Each model alone chooses its own reward9 action, yet no shared action works.
    for model in s.TINY["models"]:
        answer = s.solve487({**s.IMPOSSIBLE, "models": [model]})
        assert answer["models"][0]["reward"] == [9, 1]


def test_all_three_tick_shared_policies_in_two_deterministic_worlds():
    rng = random.Random(487_006)
    for _ in range(24):
        data = copy.deepcopy(s.TINY)
        data.update(horizon=3, energy=2)
        data["actions"].append({"id": "probe", "energy": 0})
        for m, model in enumerate(data["models"]):
            model["threshold"] = rng.randrange(2, 10)
            model["outcomes"]["probe"] = [[{"label": str(m), "reward": 0, "p": [1, 1]}]]*2
            for name in ("a", "b", "safe"):
                reward = rng.randrange(1, 9)
                model["outcomes"][name] = [[{"label": "ok", "reward": reward, "p": [1, 1]}]]*2
        actions = {a["id"]: a for a in data["actions"]}
        names = ["wait", *actions]

        def outcome(action, m, data=data):
            if action == "wait":
                return 0, "_"
            row = data["models"][m]["outcomes"][action][0][0]
            return row["reward"], action+":"+row["label"]

        best = None
        for first, second in itertools.product(names, repeat=2):
            used = [name for name in (first, second) if name != "wait"]
            if len(set(used)) != len(used):
                continue
            energy = 2-sum(actions[name]["energy"] for name in used)
            if energy < 0:
                continue
            available = [name for name in names if name == "wait" or name not in used and actions[name]["energy"] <= energy]
            observations = sorted({outcome(first, m)[1] for m in range(2)})
            for selected in itertools.product(available, repeat=len(observations)):
                choice = dict(zip(observations, selected, strict=True))
                rewards = [sum(outcome(a, m)[0] for a in (first, second, choice[outcome(first, m)[1]])) for m in range(2)]
                if any(rewards[m] < model["threshold"] for m, model in enumerate(data["models"])):
                    continue
                middle = []
                for observation in observations:
                    seconds = sorted({outcome(second, m)[1] for m in range(2) if outcome(first, m)[1] == observation})
                    middle.append({"observation": observation, "next": {"action": choice[observation], "branches": [
                        {"observation": label, "next": {"done": True}} for label in seconds]}})
                tree = {"action": first, "branches": [{"observation": "_", "next": {"action": second, "branches": middle}}]}
                key = -min(rewards), -sum(rewards), code(tree)
                expected = {"feasible": True, "models": [{"id": f"m{m}", "reward": [r, 1], "success": [1, 1]}
                                                         for m, r in enumerate(rewards)], "policy": tree}
                if best is None or key < best[0]:
                    best = key, expected
        assert s.solve487(data) == (best[1] if best else {"feasible": False})


def test_ignoring_second_model_constraint_mutant():
    source = textwrap.dedent(inspect.getsource(s.solve487))
    source = source.replace('for i, model in enumerate(models))]', 'for i, model in enumerate(models[:1]))]')
    namespace = {}
    exec(compile(source, 'mutant', 'exec'), namespace)
    assert any(namespace['solve487'](data) != expected for data, expected in s.CASES)


def test_v6_gold_lifecycle(tmp_path):
    s.TASK.setup(tmp_path)
    assert not s.TASK.verify(tmp_path).passed
    s.TASK.apply_gold(tmp_path)
    assert s.TASK.verify(tmp_path).passed
