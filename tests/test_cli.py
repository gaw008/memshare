import json
from dataclasses import replace
from pathlib import Path
import sys

from argon2 import PasswordHasher
from dotenv import dotenv_values
import pytest
from mcp.shared.auth import OAuthClientInformationFull

from memhub import cli
from memhub.auth import OAuthProvider
from memhub.permissions import PermissionPolicy
from memhub.storage import MemoryStore, initialize_memory


def test_config_flag_is_required(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["memhub", "serve"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "--config" in capsys.readouterr().err


def test_cli_uses_explicit_config_path(config, monkeypatch, capsys):
    observed = []

    def load(path):
        observed.append(path)
        return config

    monkeypatch.setattr(cli, "load_config", load)
    monkeypatch.setattr(cli, "set_password", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["memhub", "--config", "custom.toml", "set-password"])
    cli.main()
    assert observed == [Path("custom.toml")]


def test_password_hash_preserves_static_tokens_and_is_private(config, monkeypatch):
    before = dotenv_values(config.env_path, interpolate=False)["STATIC_TOKENS"]
    passwords = iter(["test-password-value", "test-password-value"])
    monkeypatch.setattr(cli, "getpass", lambda _: next(passwords))
    cli.set_password(config)
    values = dotenv_values(config.env_path, interpolate=False)
    assert PasswordHasher().verify(values["OAUTH_PASSWORD_HASH"], "test-password-value")
    assert values["STATIC_TOKENS"] == before
    assert config.env_path.stat().st_mode & 0o777 == 0o600
    passwords = iter(["replacement-password", "replacement-password"])
    cli.set_password(config)
    values = dotenv_values(config.env_path, interpolate=False)
    assert PasswordHasher().verify(values["OAUTH_PASSWORD_HASH"], "replacement-password")
    assert config.env_path.read_text().count("OAUTH_PASSWORD_HASH=") == 1


def test_mismatched_password_does_not_modify_env(config, monkeypatch):
    before = config.env_path.read_text()
    inputs = iter(["first-password", "different-password"])
    monkeypatch.setattr(cli, "getpass", lambda _: next(inputs))
    with pytest.raises(ValueError, match="match"):
        cli.set_password(config)
    assert config.env_path.read_text() == before


def test_import_counts_duplicates_and_rejects_sensitive_without_echo(config, tmp_path, monkeypatch, capsys):
    source = tmp_path / "facts.txt"
    source.write_text("- 正在做增肌训练\n* 正在做增肌训练\n\n1. 喜欢游泳\n2) 密码是 fake-secret-password\n", encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(sys, "argv", [
        "memhub", "--config", "custom.toml", "import", "--source", "gemini",
        "--path", "topics/fitness.md", "--description", "训练习惯", str(source),
    ])
    cli.main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert (result["written"], result["duplicate"], result["rejected"]) == (2, 1, 1)
    assert result["reasons"][0]["line"] == 5
    assert "fake-secret-password" not in output
    store = MemoryStore(config)
    try:
        content = store.read(["topics/fitness.md"], identity="gemini")["files"][0]["content"]
        assert "[gemini · " in content
        assert "- 喜欢游泳 [gemini · " in content
        assert "fake-secret-password" not in content
    finally:
        store.close()


@pytest.mark.parametrize("source,path", [
    ("claude-code", "profile.md"), ("grok", "areas/work.md"), ("unknown", "topics/fitness.md"),
])
def test_import_rejects_profile_and_unauthorized_source_or_path(config, tmp_path, source, path):
    text_file = tmp_path / "facts.txt"
    text_file.write_text("喜欢散步\n喜欢游泳\n", encoding="utf-8")
    result = cli.import_facts(config, source, path, text_file, "偏好")
    assert result["written"] == result["duplicate"] == 0
    assert result["rejected"] == 2
    assert len(result["reasons"]) == 2


def test_init_accepts_existing_empty_directory_and_initializes_main(config, tmp_path):
    target = tmp_path / "empty-memory"
    target.mkdir()
    initialize_memory(replace(config, memory_dir=target))
    assert (target / ".git" / "HEAD").read_text().strip() == "ref: refs/heads/main"


async def test_clients_cli_lists_persistent_binding_and_revokes_tokens(config, tmp_path, monkeypatch, capsys):
    config = replace(config, oauth_db_path=tmp_path / "oauth-admin.sqlite")
    provider = OAuthProvider(config, PermissionPolicy(config.clients_path))
    client = OAuthClientInformationFull(
        client_id="cli-admin-test-client", redirect_uris=["https://example.org/callback"],
        token_endpoint_auth_method="none", scope=" ".join(config.oauth_scopes),
    )
    try:
        await provider.register_client(client)
        # Model a completed owner authorization before administering the persisted client.
        with provider.db:
            provider.db.execute("UPDATE clients SET identity=? WHERE client_id=?", ("claude", client.client_id))
            tokens = provider._issue(client.client_id, "claude", config.oauth_scopes, provider.resource)
        assert await provider.load_access_token(tokens.access_token) is not None
    finally:
        provider.close()
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(sys, "argv", ["memhub", "--config", "custom.toml", "clients", "list"])
    cli.main()
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["client_id"] == client.client_id
    assert rows[0]["identity"] == "claude"
    assert rows[0]["last_issued_at"] > 0
    assert tokens.access_token not in str(rows)
    assert tokens.refresh_token not in str(rows)
    monkeypatch.setattr(sys, "argv", ["memhub", "--config", "custom.toml", "clients", "revoke", client.client_id])
    cli.main()
    assert client.client_id in capsys.readouterr().out
    verifier = OAuthProvider(config, PermissionPolicy(config.clients_path))
    try:
        assert await verifier.load_access_token(tokens.access_token) is None
        assert await verifier.load_refresh_token(client, tokens.refresh_token) is None
        assert verifier.clients_list()[0]["revoked"] == 1
    finally:
        verifier.close()
