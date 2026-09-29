"""Paraphrase a long task's generated texts with an LLM and validate them.

A task module opts in by defining ``REWRITE`` with:

- ``items()`` -> list of ``{"key", "draft", "brief"}``: the generated text and a
  short statement of what the rewrite must preserve (ground truth, trap roles);
- ``writer_system``: system prompt for the rewriting model;
- ``judge_messages(item, text)`` -> chat messages asking an independent model
  to solve the item from the rewritten text using only the task's rules;
- ``judge_accept(item, reply)`` -> ``(ok, feedback)``.

A rewrite is stored only when the judge recovers the ground truth; otherwise
the writer retries with the judge's feedback, and after ``--retries`` failures
the draft stays as is (the task then shows that item's template text).
Results go to ``harness_bench/long_tasks/_texts/<task_id>.json.gz`` and are
reused on the next run, so the script can be resumed.

Usage:
  OPENROUTER_BASE_URL=... OPENROUTER_API_KEY=... \\
  python scripts/rewrite_long_texts.py --module t03_review_annotation
"""

from __future__ import annotations

import argparse
import gzip
import importlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from harness_bench.long_tasks.common import TEXTS_DIR, text_sha

_lock = threading.Lock()
_cost = [0.0]


def chat(model: str, messages: list[dict], *, max_tokens: int = 4000, temperature: float | None = None) -> str:
    body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if temperature is not None:
        body["temperature"] = temperature
    if model.startswith("anthropic/"):
        body["cache_control"] = {"type": "ephemeral"}
    req = urllib.request.Request(
        os.environ["OPENROUTER_BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.load(resp)
            usage = data.get("usage") or {}
            with _lock:
                _cost[0] += float(usage.get("cost") or 0.0)
            return data["choices"][0]["message"]["content"] or ""
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, OSError):
            if attempt == 7:
                raise
            time.sleep(min(120, 10 * 2**attempt))
    raise RuntimeError("unreachable")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", required=True, help="module inside harness_bench.long_tasks")
    ap.add_argument("--writer", default="anthropic/claude-sonnet-5")
    ap.add_argument("--judge", default="openai/gpt-5.6-sol")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="only the first N pending items")
    args = ap.parse_args()

    module = importlib.import_module(f"harness_bench.long_tasks.{args.module}")
    spec = module.REWRITE
    task_id = module.TASK.id
    path = TEXTS_DIR / f"{task_id}.json.gz"
    store: dict = {}
    if path.is_file():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            store = json.load(fh)
    items = spec["items"]()
    pending = [it for it in items if store.get(it["key"], {}).get("sha") != text_sha(it["draft"])]
    if args.limit:
        pending = pending[: args.limit]
    print(f"{task_id}: {len(items)} items, {len(pending)} pending", flush=True)

    def save() -> None:
        TEXTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False, sort_keys=True)
        tmp.replace(path)

    def work(item: dict) -> tuple[str, bool, int]:
        try:
            return _work(item)
        except Exception as exc:  # noqa: BLE001 — one item must not stop the batch
            print(f"  {item['key']}: {type(exc).__name__}: {exc}", flush=True)
            return item["key"], False, 0

    def _work(item: dict) -> tuple[str, bool, int]:
        feedback = ""
        for attempt in range(1, args.retries + 1):
            user = (
                f"{item['brief']}\n\nЧерновик:\n<<<\n{item['draft']}\n>>>\n"
                + (f"\nПрошлая попытка не прошла проверку: {feedback}\n" if feedback else "")
                + "\nВерни только новый текст, без комментариев и кавычек."
            )
            text = chat(
                args.writer,
                [{"role": "system", "content": spec["writer_system"]}, {"role": "user", "content": user}],
                temperature=1.0,
            ).strip()
            if not text:
                feedback = "пустой ответ"
                continue
            reply = chat(args.judge, spec["judge_messages"](item, text), max_tokens=6000)
            ok, feedback = spec["judge_accept"](item, reply)
            if ok:
                with _lock:
                    store[item["key"]] = {"sha": text_sha(item["draft"]), "text": text}
                return item["key"], True, attempt
        return item["key"], False, args.retries

    ok_n = fail_n = done = 0
    failed: list[str] = []
    with ThreadPoolExecutor(args.concurrency) as pool:
        futures = [pool.submit(work, it) for it in pending]
        for fut in as_completed(futures):
            key, ok, attempts = fut.result()
            done += 1
            ok_n += ok
            fail_n += not ok
            if not ok:
                failed.append(key)
            if done % 20 == 0 or done == len(pending):
                with _lock:
                    save()
                print(f"  {done}/{len(pending)} accepted={ok_n} failed={fail_n} cost=${_cost[0]:.2f}", flush=True)
    with _lock:
        save()
    covered = sum(1 for it in items if store.get(it["key"], {}).get("sha") == text_sha(it["draft"]))
    print(f"done: coverage {covered}/{len(items)}, failed now {failed[:30]}, cost ${_cost[0]:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
