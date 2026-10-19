from dataclasses import replace

import pytest

from memhub.permissions import PermissionPolicy
from memhub.storage import MemoryStore


def seed_private_facts(store):
    for path in ("people/friend.md", "areas/work.md", "topics/social-media.md"):
        store.remember(path, "我喜欢散步", "claude-code", description="散步记录")


def test_grok_cannot_read_people_and_cannot_write_anything(store):
    seed_private_facts(store)
    with pytest.raises(PermissionError, match="cannot read people"):
        store.read(["people/friend.md"], identity="grok")
    for tool in (
        lambda: store.remember("topics/social-media.md", "每周游泳", "grok"),
        lambda: store.update("topics/social-media.md", "散步", "游泳", "version", "grok"),
        lambda: store.forget("topics/social-media.md", "我喜欢散步", "grok"),
    ):
        with pytest.raises(PermissionError, match="cannot write"):
            tool()


def test_grok_catalog_and_search_hide_people_areas_before_limit(store):
    seed_private_facts(store)
    context = store.get_context(identity="grok")
    assert set(context["files"]) == {
        "profile.md", "preferences.md", "topics/social-media.md"
    }
    hits = store.search("散步", limit=1, identity="grok")["hits"]
    assert len(hits) == 1
    assert hits[0]["path"] == "topics/social-media.md"


def test_chatgpt_reads_and_writes_all(store):
    seed_private_facts(store)
    assert store.remember("areas/science.md", "开展研究", "chatgpt", description="科学项目")["status"] == "written"
    assert store.read(["people/friend.md"], identity="chatgpt")["files"]
    assert store.remember("topics/science.md", "喜欢研究物理", "chatgpt", description="科学兴趣")["status"] == "written"


@pytest.mark.parametrize("action", ["context", "read", "search", "remember"])
def test_unknown_identity_is_rejected_even_when_no_search_results(store, action):
    with pytest.raises(PermissionError, match="Unknown or unbound"):
        if action == "context":
            store.get_context(identity="unknown")
        elif action == "read":
            store.read([], identity="unknown")
        elif action == "search":
            store.search("", identity="unknown")
        else:
            store.remember("topics/fact.md", "喜欢散步", "unknown", description="偏好")


def test_context_does_not_load_profile_or_preferences_without_read_permission(config, tmp_path):
    path = tmp_path / "restricted-clients.yaml"
    path.write_text("restricted:\n  read: ['topics/*.md']\n  write: []\n", encoding="utf-8")
    # Missing files prove permission filtering precedes document reads.
    (config.memory_dir / "profile.md").unlink()
    (config.memory_dir / "preferences.md").unlink()
    store = MemoryStore(replace(config, clients_path=path))
    try:
        result = store.get_context(identity="restricted")
        assert "profile" not in result and "preferences" not in result
        assert result["files"] == {}
    finally:
        store.close()


def test_write_only_identity_cannot_inspect_duplicate_or_conflict_contents(config, tmp_path):
    path = tmp_path / "write-only-clients.yaml"
    path.write_text("writer:\n  read: []\n  write: ['topics/*.md']\n", encoding="utf-8")
    store = MemoryStore(replace(config, clients_path=path))
    try:
        store.remember("topics/private.md", "喜欢散步", "writer", description="长期偏好")
        duplicate = store.remember("topics/private.md", "喜欢散步", "writer")
        assert duplicate["status"] == "duplicate"
        assert "candidates" not in duplicate
        conflict = store.update("topics/private.md", "散步", "喜欢游泳", "stale", "writer")
        assert conflict["status"] == "conflict"
        assert "content" not in conflict
        assert "喜欢散步" not in str(duplicate) + str(conflict)
    finally:
        store.close()


@pytest.mark.parametrize("text", ["{}", "client: {read: ['*']}", "client: {read: '*', write: []}"])
def test_invalid_policy_has_explicit_error(tmp_path, text):
    path = tmp_path / "clients.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        PermissionPolicy(path)
