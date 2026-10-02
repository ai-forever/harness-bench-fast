"""Policies must hold in the recorded history, not merely the final state."""

from harness_bench.reflect_tasks import t09_wiki_optimistic_lock as wiki
from harness_bench.reflect_tasks import t20_admin_elevation as iam
from harness_bench.reflect_tasks.common import call, replay


def test_documented_user_show_and_elevation_accounting(tmp_path):
    iam.TASK.setup(tmp_path)
    response, code = call(tmp_path, iam.SERVICE, "user", "show", "--user", "alice")
    assert code == 0
    assert response["effective"]["repo:read"] == ["group:developers"]
    call(tmp_path, iam.SERVICE, "elevate", "--reason", "CHG-2291")
    response, code = call(tmp_path, iam.SERVICE, "user", "show", "--user", "alice")
    assert code == 0 and response["elevated_calls_left"] == 4


def test_reversed_changes_rejected_even_with_correct_final_permissions(tmp_path):
    iam.TASK.setup(tmp_path)
    rows = (tmp_path / "changes.csv").read_text().splitlines()
    (tmp_path / "changes.csv").write_text("\n".join([rows[0], *reversed(rows[1:])]) + "\n")
    iam.TASK.apply_gold(tmp_path)
    state = replay(tmp_path, iam.SERVICE).state
    assert iam._effective(state["users"], state["groups"]) == iam.TARGET
    result = iam.TASK.verify(tmp_path)
    assert not result.passed and "order" in result.message


def test_temporary_group_permission_change_rejected(tmp_path):
    iam.TASK.setup(tmp_path)
    iam.TASK.apply_gold(tmp_path)
    assert iam.TASK.verify(tmp_path).passed
    call(tmp_path, iam.SERVICE, "elevate", "--reason", "CHG-2291")
    for action in ("grant", "revoke"):
        response, code = call(tmp_path, iam.SERVICE, "group", action, "--group",
                              "developers", "--perm", "audit:temporary")
        assert code == 0 and response["applied"]
    result = iam.TASK.verify(tmp_path)
    assert not result.passed and "group permissions" in result.message


def test_wiki_moving_block_before_existing_bot_text_rejected(tmp_path):
    wiki.TASK.setup(tmp_path)
    wiki.TASK.apply_gold(tmp_path)
    assert wiki.TASK.verify(tmp_path).passed
    page = "runbook-db"
    lines, _, consistent = wiki._read_full(tmp_path, page)
    assert consistent
    mine = wiki.EDITS[page]
    base = [line for line in lines if line not in mine]
    index = next(i for i, line in enumerate(base) if line.startswith("<!-- linkbot:"))
    assert wiki._save(tmp_path, page, base[:index] + mine + base[index:])["applied"]
    result = wiki.TASK.verify(tmp_path)
    assert not result.passed and "not appended" in result.message


def test_wiki_bot_append_after_user_block_is_allowed(tmp_path):
    wiki.TASK.setup(tmp_path)
    page = "release-notes"
    call(tmp_path, wiki.SERVICE, "get", "--page", page)
    rel = wiki._write(tmp_path, page, wiki.EDITS[page])
    response, code = call(tmp_path, wiki.SERVICE, "save", "--page", page,
                          "--file", rel, "--append")
    assert code == 0 and response["applied"]
    for _ in range(2):
        call(tmp_path, wiki.SERVICE, "pages")
    for name, mine in wiki.EDITS.items():
        if name == page:
            continue
        while True:
            lines, _, consistent = wiki._read_full(tmp_path, name)
            if consistent and wiki._save(tmp_path, name, wiki._strip(lines) + mine).get("applied"):
                break
    lines = replay(tmp_path, wiki.SERVICE).state["pages"][page]["lines"]
    assert lines.index(wiki.EDITS[page][-1]) < lines.index("<!-- linkbot: проверено 3 ссылки -->")
    assert wiki.TASK.verify(tmp_path).passed


def test_iam_inherited_revoke_without_literal_revoke_is_allowed(tmp_path):
    iam.TASK.setup(tmp_path)
    console = iam._Console(tmp_path)
    groups = call(tmp_path, iam.SERVICE, "groups")[0]["groups"]
    users = {
        user: call(tmp_path, iam.SERVICE, "user", "show", "--user", user)[0]
        for user in iam.USERS
    }
    expected = iam._effective(iam.USERS, iam.GROUPS)
    for action, user, perm in iam.CHANGES:
        sources = [group for group in users[user]["groups"] if perm in groups[group]]
        if action == "revoke" and sources:
            # Restore the other rights before leaving the groups. There is no
            # need to issue a revoke that the service would refuse to apply.
            for keep in sorted({p for group in sources for p in groups[group]} - {perm}):
                console.run("grant", "--user", user, "--perm", keep)
            for group in sources:
                console.run("group", "remove-member", "--group", group, "--user", user)
            if perm in users[user]["direct"]:
                console.run("revoke", "--user", user, "--perm", perm)
        else:
            console.run(action, "--user", user, "--perm", perm)
        (expected[user].add if action == "grant" else expected[user].discard)(perm)
        state = replay(tmp_path, iam.SERVICE).state
        assert iam._effective(state["users"], state["groups"]) == expected
    result = iam.TASK.verify(tmp_path)
    assert result.passed, result.message
