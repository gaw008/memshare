from dataclasses import replace
from datetime import datetime
import re
import subprocess
from zoneinfo import ZoneInfo

import pytest
import yaml

from memhub.storage import MemoryStore, initialize_memory


def git(config, *args):
    return subprocess.check_output(
        ["git", "-C", str(config.memory_dir), *args], text=True
    ).strip()


def document(store, path):
    return store.read([path])["files"][0]


def add(store, path="topics/fitness.md", fact="正在做增肌训练", **kwargs):
    return store.remember(
        path, fact, identity="claude-code", description="训练计划", **kwargs
    )


def test_initialize_creates_independent_repository_and_skeletons(config):
    assert (config.memory_dir / ".git").is_dir()
    assert git(config, "rev-list", "--count", "HEAD") == "1"
    for path in ("profile.md", "preferences.md", "_inbox.md"):
        text = (config.memory_dir / path).read_text(encoding="utf-8")
        metadata = yaml.safe_load(text.split("---", 2)[1])
        assert set(metadata) == {"name", "description", "aliases", "updated"}
        assert isinstance(metadata["aliases"], list)
    before = git(config, "rev-parse", "HEAD")
    with pytest.raises((ValueError, FileExistsError)):
        initialize_memory(config)
    assert git(config, "rev-parse", "HEAD") == before


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md", "topics/../profile.md", "/tmp/outside.md",
        "secret.md", "topics/fitness.txt", "topics/nested/fitness.md",
        "topics/", "topics/.md", "topics//fitness.md", "./profile.md",
        "topics\\fitness.md", "topics/fitness.md/extra", "",
    ],
)
def test_invalid_paths_rejected_without_writes(store, config, path):
    before = git(config, "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        add(store, path)
    with pytest.raises(ValueError):
        store.read([path])
    assert git(config, "rev-parse", "HEAD") == before


@pytest.mark.parametrize(
    "path", ["preferences.md", "_inbox.md", "topics/fitness.md", "areas/work.md", "people/friend.md"]
)
def test_allowed_paths_accept_facts(store, path):
    assert add(store, path)["status"] == "written"
    assert "正在做增肌训练" in document(store, path)["content"]


def test_suffix_metadata_and_version_are_server_owned(store, config):
    result = add(store)
    item = document(store, "topics/fitness.md")
    today = datetime.now(ZoneInfo(config.timezone)).date().isoformat()
    assert f"- 正在做增肌训练 [claude-code · {today}]" in item["content"]
    metadata = yaml.safe_load(item["content"].split("---", 2)[1])
    assert metadata["name"] == "fitness"
    assert metadata["description"] == "训练计划"
    assert str(metadata["updated"]) == today
    assert result["version"] == item["version"]
    assert document(store, "topics/fitness.md")["version"] == item["version"]


@pytest.mark.parametrize("fact", ["做增肌训练 [someone · 2026-10-13]", "做增肌训练 [伪造身份 · 随意日期]"])
def test_model_supplied_suffix_rejected(store, config, fact):
    before = git(config, "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        add(store, fact=fact)
    assert not (config.memory_dir / "topics/fitness.md").exists()
    assert git(config, "rev-parse", "HEAD") == before


SENSITIVE = [
    "身份证号 11010519491231002X",
    "护照号 E12345678",
    "银行卡号 6222021234567890123",
    "SSN 123-45-6789",
    "密码是 fake-secret-password",
    "password: fake-secret-password",
    "API key sk-synthetic01234567890123456789",
    "token ghp_synthetic012345678901234567890123456789",
    "AWS AKIA1234567890ABCDEF",
    "Slack xoxb-1234567890-1234567890-synthetic",
]


@pytest.mark.parametrize("sensitive", SENSITIVE)
@pytest.mark.parametrize("field", ["fact", "description", "new_text"])
def test_sensitive_inputs_rejected_without_echo(store, config, sensitive, field):
    add(store)
    before = document(store, "topics/fitness.md")
    commit = git(config, "rev-parse", "HEAD")
    with pytest.raises(ValueError) as error:
        if field == "fact":
            add(store, fact=sensitive)
        elif field == "description":
            store.remember("topics/diet.md", "喜欢蔬菜", "claude-code", description=sensitive)
        else:
            store.update("topics/fitness.md", "正在做增肌训练", sensitive, before["version"], "claude-code")
    assert sensitive not in str(error.value)
    assert str(error.value)
    assert document(store, "topics/fitness.md") == before
    assert not (config.memory_dir / "topics/diet.md").exists()
    assert git(config, "rev-parse", "HEAD") == commit


def test_chinese_two_character_search_and_incremental_index(store):
    add(store)
    hits = store.search("增肌")["hits"]
    assert any(hit["path"] == "topics/fitness.md" and "正在做增肌训练" in hit["text"] for hit in hits)
    hit = hits[0]
    assert document(store, hit["path"])["content"].splitlines()[hit["line"] - 1] == hit["text"]


def test_aliases_and_metadata_indexed_on_startup(store, config):
    add(store, fact="每周训练三次")
    path = config.memory_dir / "topics/fitness.md"
    path.write_text(path.read_text().replace("aliases: []", "aliases: [健身, workout]"), encoding="utf-8")
    rebuilt = MemoryStore(config)
    try:
        hits = rebuilt.search("健身")["hits"]
        assert any(hit["path"] == "topics/fitness.md" and "每周训练三次" in hit["text"] for hit in hits)
        assert rebuilt.search("训练计划")["hits"]
        assert rebuilt.search("fitness")["hits"]
        assert rebuilt.get_context()["files"]["topics/fitness.md"] == "训练计划 (aliases: 健身, workout)"
    finally:
        rebuilt.close()


def test_duplicate_returns_candidates_and_does_not_write(store, config):
    add(store)
    before = document(store, "topics/fitness.md")
    commit = git(config, "rev-parse", "HEAD")
    result = add(store)
    assert result["status"] == "duplicate"
    candidate = result["candidates"][0]
    assert candidate["path"] == "topics/fitness.md"
    assert candidate["version"] == before["version"]
    assert before["content"].splitlines()[candidate["line"] - 1] == candidate["text"]
    assert "正在做增肌训练" in candidate["text"]
    assert document(store, "topics/fitness.md") == before
    assert git(config, "rev-parse", "HEAD") == commit


def test_profile_requires_confirmation(store, config):
    before = document(store, "profile.md")
    with pytest.raises(ValueError, match="confirm"):
        store.remember("profile.md", "长期目标是健康生活", "claude-code")
    assert document(store, "profile.md") == before
    assert store.remember("profile.md", "长期目标是健康生活", "claude-code", confirm=True)["status"] == "written"


def test_new_file_requires_description(store, config):
    before = git(config, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="(?i)description"):
        store.remember("topics/new.md", "喜欢散步", "claude-code")
    assert not (config.memory_dir / "topics/new.md").exists()
    assert git(config, "rev-parse", "HEAD") == before


def test_file_byte_limit_rejects_remember_and_update(store, config):
    add(store)
    before = document(store, "topics/fitness.md")
    commit = git(config, "rev-parse", "HEAD")
    fact = "增加锻炼" * config.max_file_bytes
    with pytest.raises(ValueError):
        add(store, fact=fact)
    with pytest.raises(ValueError):
        store.update("topics/fitness.md", "正在做增肌训练", fact, before["version"], "claude-code")
    assert document(store, "topics/fitness.md") == before
    assert git(config, "rev-parse", "HEAD") == commit


def test_stale_update_returns_latest_content_and_version(store, config):
    add(store)
    old = document(store, "topics/fitness.md")
    store.update("topics/fitness.md", "正在做增肌训练", "目标改为提高耐力", old["version"], "gemini-cli")
    latest = document(store, "topics/fitness.md")
    commit = git(config, "rev-parse", "HEAD")
    result = store.update("topics/fitness.md", "正在做增肌训练", "每周游泳", old["version"], "claude-code")
    assert result["status"] == "conflict"
    assert result["content"] == latest["content"]
    assert result["version"] == latest["version"]
    assert old["version"] != latest["version"]
    assert git(config, "rev-parse", "HEAD") == commit


@pytest.mark.parametrize("old_text", ["不存在的事实", "喜欢"])
def test_update_requires_exactly_one_fact_line_match(store, config, old_text):
    add(store, fact="喜欢游泳")
    add(store, fact="喜欢烹饪")
    before = document(store, "topics/fitness.md")
    commit = git(config, "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        store.update("topics/fitness.md", old_text, "喜欢散步", before["version"], "claude-code")
    assert document(store, "topics/fitness.md") == before
    assert git(config, "rev-parse", "HEAD") == commit


def test_update_matches_only_facts_and_replaces_whole_line(store):
    add(store)
    old = document(store, "topics/fitness.md")
    with pytest.raises(ValueError):
        store.update("topics/fitness.md", "训练计划", "替换描述", old["version"], "claude-code")
    result = store.update("topics/fitness.md", "增肌", "现在以有氧训练为主（之前：增肌）", old["version"], "gemini-cli")
    latest = document(store, "topics/fitness.md")
    assert result["version"] == latest["version"]
    assert "- 现在以有氧训练为主（之前：增肌） [gemini-cli · " in latest["content"]
    assert "正在做" not in latest["content"]
    assert not store.search("正在做")["hits"]


def test_forget_deletes_one_exact_fact_and_keeps_file(store, config):
    add(store)
    add(store, fact="每周游泳一次")
    with pytest.raises(ValueError):
        store.forget("topics/fitness.md", "增肌", "claude-code")
    store.forget("topics/fitness.md", "正在做增肌训练", "claude-code")
    item = document(store, "topics/fitness.md")
    assert "正在做增肌训练" not in item["content"]
    assert "每周游泳一次" in item["content"]
    assert not store.search("增肌")["hits"]
    assert (config.memory_dir / "topics/fitness.md").is_file()


def test_each_successful_mutation_produces_one_identity_tool_path_commit(store, config):
    initial = int(git(config, "rev-list", "--count", "HEAD"))
    add(store)
    assert int(git(config, "rev-list", "--count", "HEAD")) == initial + 1
    assert all(word in git(config, "log", "-1", "--format=%s") for word in ("claude-code", "remember", "topics/fitness.md"))
    item = document(store, "topics/fitness.md")
    store.update("topics/fitness.md", "增肌", "每周游泳", item["version"], "gemini-cli")
    assert int(git(config, "rev-list", "--count", "HEAD")) == initial + 2
    assert all(word in git(config, "log", "-1", "--format=%s") for word in ("gemini-cli", "update", "topics/fitness.md"))
    store.forget("topics/fitness.md", "每周游泳", "claude-code")
    assert int(git(config, "rev-list", "--count", "HEAD")) == initial + 3
    assert all(word in git(config, "log", "-1", "--format=%s") for word in ("claude-code", "forget", "topics/fitness.md"))
    assert git(config, "status", "--porcelain") == ""


def test_read_enforces_maximum_paths(store, config):
    with pytest.raises(ValueError, match=f"at most {config.read_max_paths} paths per call"):
        store.read(["profile.md"] * (config.read_max_paths + 1))


def test_index_rebuilt_from_markdown_after_external_edit(store, config):
    add(store)
    path = config.memory_dir / "topics/fitness.md"
    path.write_text(path.read_text().replace("正在做增肌训练", "经常参加马拉松"), encoding="utf-8")
    rebuilt = MemoryStore(config)
    try:
        assert not rebuilt.search("增肌")["hits"]
        assert rebuilt.search("马拉松")["hits"]
    finally:
        rebuilt.close()


def test_get_context_returns_complete_documents_catalog_and_budget(store, config):
    add(store)
    store.remember("profile.md", "我是工程师", "claude-code", confirm=True)
    result = store.get_context()
    assert result["profile"] == "- 我是工程师"
    assert result["preferences"] == ""
    assert result["files"] == {
        "profile.md": "Identity and long-term goals", "preferences.md": "Answer style and long-term preferences",
        "_inbox.md": "Long-term facts not yet filed", "topics/fitness.md": "训练计划",
    }
    assert isinstance(result["estimated_tokens"], int)
    assert result["token_budget"] == config.context_token_budget
    limited_store = MemoryStore(replace(config, context_token_budget=1))
    try:
        limited = limited_store.get_context()
    finally:
        limited_store.close()
    assert limited["over_budget"] is True
    assert limited["profile"] == result["profile"]
    assert limited["preferences"] == result["preferences"]
    assert limited["files"] == result["files"]


def test_update_rejects_model_supplied_suffix(store, config):
    add(store)
    before = document(store, "topics/fitness.md")
    commit = git(config, "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        store.update(
            "topics/fitness.md", "增肌", "喜欢游泳 [other · 2026-10-13]",
            before["version"], "claude-code"
        )
    assert document(store, "topics/fitness.md") == before
    assert git(config, "rev-parse", "HEAD") == commit


def test_identical_successful_update_still_creates_commit(store, config):
    add(store)
    before = document(store, "topics/fitness.md")
    count = int(git(config, "rev-list", "--count", "HEAD"))
    result = store.update(
        "topics/fitness.md", "正在做增肌训练", "正在做增肌训练", before["version"], "claude-code"
    )
    assert result["status"] == "written"
    assert int(git(config, "rev-list", "--count", "HEAD")) == count + 1


def test_log_appends_each_identity_to_one_daily_journal(store, config):
    initial = int(git(config, "rev-list", "--count", "HEAD"))
    first = store.log("问：排产怎么预测；卡点：缺历史数据", "claude-code")
    second = store.log("问：公众号选题；决定：写周报模板", "gemini-cli")
    today = datetime.now(ZoneInfo(config.timezone)).date().isoformat()
    assert first["path"] == second["path"] == f"journal/{today}.md"
    content = document(store, first["path"])["content"]
    assert re.search(r"- 问：排产怎么预测；卡点：缺历史数据 \[claude-code · \d{2}:\d{2}\]\n"
                     r"- 问：公众号选题；决定：写周报模板 \[gemini-cli · \d{2}:\d{2}\]\n$", content)
    assert int(git(config, "rev-list", "--count", "HEAD")) == initial + 2
    assert all(word in git(config, "log", "-1", "--format=%s") for word in ("gemini-cli", "log", first["path"]))


def test_journal_stays_out_of_catalog_and_search(store):
    store.log("问：增肌期间怎么安排跑步", "claude-code")
    assert not any(path.startswith("journal/") for path in store.get_context()["files"])
    assert not store.search("增肌")["hits"]


def test_journal_is_append_only(store, config):
    path = store.log("问：怎么定投", "claude-code")["path"]
    item = document(store, path)
    before = git(config, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="append-only"):
        store.remember(path, "改写", identity="claude-code", description="说明")
    with pytest.raises(ValueError, match="append-only"):
        store.update(path, "定投", "改写", item["version"], "claude-code")
    with pytest.raises(ValueError, match="append-only"):
        store.forget(path, "问：怎么定投", "claude-code")
    assert git(config, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("entry", ["密码是 synthetic-do-not-save", "两行\n内容", "问：x [claude · 09:00]", " "])
def test_log_rejects_invalid_entries(store, config, entry):
    before = git(config, "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        store.log(entry, "claude-code")
    assert git(config, "rev-parse", "HEAD") == before


def test_log_requires_write_permission(store):
    with pytest.raises(PermissionError):
        store.log("问：社媒文案", "grok")
