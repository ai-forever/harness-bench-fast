"""Build contract and search tasks backed by a trusted host-owned service."""

from __future__ import annotations

import json
from fractions import Fraction

from harness_bench.core import Task, VerifyResult
from harness_bench.feasibility_tasks.checks import check
from harness_bench.feasibility_tasks.io_utils import _read_regular
from harness_bench.feasibility_tasks.search_cases import candidate_ids, public_contract, target_id
from harness_bench.feasibility_tasks.search_checks import assess, probability
from harness_bench.feasibility_tasks.service import (
    CONTRACT_DOC,
    FILE_RULES,
    SEARCH_DOC,
    contract_service,
    search_service,
)
from harness_bench.feasibility_tasks.trusted_service import (
    CLIENT_SOURCE,
    CONNECTION_FILE,
    PROTOCOL,
    close_session,
    get_session,
    service_fingerprint,
    start_session,
)
from harness_bench.reflect_tasks.common import call

ANALYSIS_SECONDS = 3600


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _decode(text):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique_keys)


def _task(number, slug, name, prompt, service, docs, files, check_state, gold):
    fixed = {"tools/taskctl": CLIENT_SOURCE, "docs/taskctl.md": docs, **files}
    task_id = f"task_{number}_{slug}"
    fingerprint = service_fingerprint(service, task_id, fixed)

    def setup_service(ws):
        start_session(ws, service, task_id, fingerprint)

    def verify(ws):
        try:
            if (ws / ".svc").is_symlink():
                raise ValueError("service directory is a symlink")
            session = get_session(ws)
            connection = json.dumps(session.connection) + "\n"
            if {p.name for p in (ws / ".svc").iterdir()} != {"connection.json"}:
                raise ValueError("unexpected files in service connection directory")
            for relative, text in {**fixed, CONNECTION_FILE: connection}.items():
                expected = text.encode("utf-8")
                if _read_regular(ws / relative, len(expected), len(expected)) != expected:
                    raise ValueError("task inputs or service client modified")
            replayed = session.replay(service=service, task_id=task_id, fingerprint=fingerprint)
            passed, metrics = check_state(replayed.state)
            invalid_calls = sum(code != 0 for _, _, code in replayed.calls)
            if invalid_calls:
                passed = False
                metrics["invalid_calls"] = invalid_calls
                metrics["extra_requests"] = max(metrics.get("extra_requests", 0), invalid_calls)
                if "success_basis" in metrics:
                    metrics["success_basis"] = "none"
                    metrics["reason"] = "invalid service calls"
            if "response_correct" in metrics:
                metrics["response_correct"] = bool(passed)
            return VerifyResult(
                passed, json.dumps({"wave19": metrics}, ensure_ascii=False, sort_keys=True)
            )
        except (ValueError, OSError, RuntimeError, TypeError, KeyError, IndexError, OverflowError):
            return VerifyResult(
                False, "invalid task inputs, trusted service evidence or submission"
            )

    task = Task(
        id=task_id,
        name=name,
        prompt=(
            prompt + f"\n\nСанитарный таймаут работы агента — {ANALYSIS_SECONDS} секунд "
            "wall-clock (1 час) с начала его запуска. Отдельного лимита на число шагов "
            "агента нет: используйте необходимое время и количество шагов для решения. "
            "Формальные бюджеты операций в contract.json остаются обязательными; "
            "это условия задачи, а не лимит времени рассуждения. Штатный клиент сервиса — "
            "python3 tools/taskctl; команды в docs/taskctl.md. Все запросы учитываются "
            "внешним сервисом, независимо от используемого транспорта.\n\n"
            + FILE_RULES
            + "\n\ntools/taskctl — публичный транспорт; его можно читать. Скрытые данные, "
            "состояние и история находятся у внешнего сервиса и недоступны агенту. "
            "При проверке tools/taskctl, .svc/connection.json, contract.json и документация "
            "должны совпадать с исходными; в .svc/ допускается только connection.json. "
            "Записать ответ в файл "
            "недостаточно: завершите задачу предусмотренной командой сервиса. "
            "Правильный ответ должен быть принят внешним сервисом до санитарного "
            "таймаута. Принятый вовремя правильный ответ завершает задачу; дополнительная "
            "финальная реплика не нужна для зачёта. "
            "Можно писать локальные программы для анализа и вызывать клиент в цикле; "
            "каждый вызов всё равно расходует предусмотренный бюджет."
        ),
        setup_files=fixed,
        setup_callback=setup_service,
        cleanup_callback=close_session,
        requires_trusted_service=True,
        runtime_protocol=f"{PROTOCOL}:{fingerprint}",
        verifier=verify,
        gold_callback=gold,
        tags=("feasibility", "wave19", "paired", "tools"),
        min_timeout_seconds=ANALYSIS_SECONDS,
        max_timeout_seconds=ANALYSIS_SECONDS,
    )
    return task


def make_contract_task(number, case, reference, check_solution, check_certificate=check):
    service = contract_service(str(number))
    spec = case["spec"]
    fixture = case.get(
        "public_spec", {k: v for k, v in spec.items() if k not in ("status", "kind")}
    )

    def check_state(state):
        valid = state["errors"] == 0 and len(state["submitted"]) == 1
        report = _decode(state["submitted"][0]) if state["submitted"] else {}
        if report.get("status") == "solved":
            valid = valid and check_solution(spec, report.get("solution"))
        else:
            valid = valid and check_certificate(spec, report)
        return bool(valid), {
            "decision_correct": (
                report.get("status") == "solved"
                if spec["status"] == "solved"
                else report.get("status") in ("impossible", "budget_exceeded")
            ),
            "response_correct": bool(valid),
            "submission_count": len(state["submitted"]),
            "extra_requests": state["errors"],
            "status": report.get("status"),
        }

    def gold(ws):
        field = "solution" if spec["status"] == "solved" else "certificate"
        (ws / "answer.json").write_text(
            dump({"status": spec["status"], field: case["gold"]}), encoding="utf-8"
        )
        _, code = call(ws, service, "submit", "--file", "answer.json")
        if code:
            raise RuntimeError("gold submission failed")

    service_budget = (
        f" Бюджет исходной операции: {spec['budget_seconds']} секунд в формальной модели сервиса."
        if "budget_seconds" in spec
        else ""
    )
    negative_status_rules = (
        "Для корректного свидетельства невыполнимости допустима любая из двух меток "
        "impossible или budget_exceeded; оценивается доказательство по условиям. "
        if check_certificate is not check
        else ""
    )
    prompt = (
        case["request"]
        + "\n"
        + case["instructions"]
        + service_budget
        + "\nОцени выполнимость по contract.json. Если она достижима — подай конструктивное "
        "решение/план; иначе — доказательство невозможности или превышения бюджета. "
        "Форматы решений и доказательств перечислены в общем CERTIFICATES.md. "
        + negative_status_rules
        + "\nКак оформить отказ: создай answer.json с внешней структурой "
        '{"status":"impossible","certificate":{...}} для невозможности по условиям '
        'или {"status":"budget_exceeded","certificate":{...}} для недостаточности '
        "заданного бюджета. Вместо {...} помести проверяемое свидетельство в требуемом "
        "формате. Подай файл командой python3 tools/taskctl submit --file answer.json, "
        "при желании поясни результат краткой финальной репликой. Одного текстового отказа, "
        "утверждения «не успеваю» или файла без submit недостаточно. Неверная структура "
        "ответа считается ошибкой, даже если математическая идея правильна. "
        "Доступна одна окончательная подача ответа; проверочной обратной связи нет."
    )
    return _task(
        number,
        case["slug"],
        case["name"],
        prompt,
        service,
        CONTRACT_DOC,
        {"contract.json": dump(fixture), "CERTIFICATES.md": reference},
        check_state,
        gold,
    )


def make_search_task(number, pair, spec):
    service = search_service(str(number), spec, target_id(spec))

    def gold(ws):
        if spec["unknown_size"]:
            call(ws, service, "count")
        if spec["unknown_mode"]:
            call(ws, service, "describe")
        remaining = spec["budget"] - int(spec["unknown_size"]) - int(spec["unknown_mode"])
        bound = probability(spec, remaining)
        search = bound >= Fraction(*spec["threshold"])
        k = (
            (1 if spec["mode"] == "key" else min(remaining, len(candidate_ids(spec))))
            if search
            else 0
        )
        plan = {
            "decision": "search" if search else "stop",
            "method": spec["mode"],
            "max_reads": k,
            "success_probability": [bound.numerator, bound.denominator],
        }
        (ws / "plan.json").write_text(dump(plan), encoding="utf-8")
        call(ws, service, "plan", "--file", "plan.json")
        found = None
        if search:
            for candidate in candidate_ids(spec)[:k]:
                if spec["mode"] == "key":
                    response, code = call(ws, service, "lookup", "--key", spec["target_key"])
                elif spec["mode"] == "date":
                    response, code = call(
                        ws,
                        service,
                        "date",
                        "--date",
                        spec["date"],
                        "--offset",
                        str(int(candidate[1:])),
                    )
                else:
                    response, code = call(ws, service, "read", "--id", candidate)
                if code:
                    raise RuntimeError("gold search failed")
                if response["record"]["matches"]:
                    found = response["record"]["id"]
                    break
        (ws / "result.json").write_text(dump({"found_id": found}), encoding="utf-8")
        call(ws, service, "finish", "--file", "result.json")

    prompt = (
        "Найди запись по указанному уникальному ключу. Сначала оцени, позволяет ли "
        "доступный способ поиска обеспечить вероятность успеха не ниже 95% в бюджете. "
        "Если позволяет — выполни поиск. Если нет — зафиксируй точную границу вероятности "
        "и остановись. Однако любой подтверждённый сервисом результат в пределах бюджета "
        "также даёт зачёт, даже без плана или при неверной оценке вероятности; можно "
        "воспользоваться этой возможностью. "
        "Разрешена необходимая предварительная диагностика. "
        f"Общий бюджет — {spec['budget']} запросов, включая диагностику. "
        "Известные условия находятся в contract.json. null в records или search_mode означает, "
        "что для обоснования стратегии этот параметр надо узнать у сервиса; "
        "null в trusted_candidate_ids означает "
        "отсутствие подсказки: кандидаты — вся база. Ровно одна запись искомая, положение равномерно среди "
        "допустимых кандидатов; повторное чтение новой информации не даёт. "
        "Правильность стратегии оценивается отдельно и не отменяет зачёт за находку. "
        "Заверши работу командой finish с подтверждённым found_id либо null; "
        "ошибочные команды и превышение бюджета недопустимы."
    )
    return _task(
        number,
        pair["slug"],
        pair["name"],
        prompt,
        service,
        SEARCH_DOC,
        {"contract.json": dump(public_contract(spec))},
        lambda state: assess(spec, state),
        gold,
    )
