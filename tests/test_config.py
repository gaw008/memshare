from dataclasses import fields
from pathlib import Path

import pytest

from memhub.config import Config, load_config, load_tokens


@pytest.mark.parametrize("missing", [field.name for field in fields(Config)])
def test_every_configuration_parameter_is_required(tmp_path, missing):
    root = Path(__file__).resolve().parents[1]
    text = (root / "memhub.toml").read_text(encoding="utf-8")
    if missing == "sensitive_patterns":
        start = text.index("sensitive_patterns =")
        end = text.index("\n]", start) + 2
        text = text[:start] + text[end:]
    elif missing == "deployment":
        text = text[:text.index("[deployment]")]
    else:
        text = "\n".join(line for line in text.splitlines() if not line.startswith(f"{missing} ="))
    path = tmp_path / "memhub.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=missing):
        load_config(path)


def test_tokens_are_loaded_directly_from_secret_file(config, monkeypatch):
    monkeypatch.setenv("STATIC_TOKENS", '{"untrusted-process-token":"intruder"}')
    assert load_tokens(config.env_path) == {
        "long-test-token": "claude-code", "other-test-token": "gemini-cli"
    }
