from dataclasses import replace
from pathlib import Path

import pytest

from memhub.config import load_config
from memhub.storage import MemoryStore, initialize_memory


@pytest.fixture
def config(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = replace(
        load_config(root / "memhub.toml"),
        memory_dir=tmp_path / "memory",
        index_path=tmp_path / "index.sqlite",
        env_path=tmp_path / ".env",
        oauth_db_path=tmp_path / "oauth.sqlite",
        access_log_path=tmp_path / "access.jsonl",
        clients_path=root / "tests" / "clients.yaml",
    )
    config.env_path.write_text(
        'STATIC_TOKENS=\'{"long-test-token":"claude-code",'
        '"other-test-token":"gemini-cli"}\'\n',
        encoding="utf-8",
    )
    initialize_memory(config)
    return config


@pytest.fixture
def store(config):
    instance = MemoryStore(config)
    try:
        yield instance
    finally:
        instance.close()
