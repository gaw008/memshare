"""Identity-scoped Markdown path permissions loaded from clients.yaml."""

from fnmatch import fnmatchcase
from pathlib import Path

import yaml


class PermissionPolicy:
    def __init__(self, path: Path):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            raise ValueError("clients.yaml must contain an identity-to-permissions mapping")
        for identity, rules in data.items():
            if not isinstance(identity, str) or not identity or not isinstance(rules, dict):
                raise ValueError("Invalid identity entry in clients.yaml")
            if set(rules) != {"read", "write"}:
                raise ValueError(f"Identity {identity} must define read and write permissions")
            for patterns in rules.values():
                if not isinstance(patterns, list) or not all(isinstance(pattern, str) and pattern for pattern in patterns):
                    raise ValueError(f"Invalid path globs for identity {identity}")
        self._rules = data

    @property
    def identities(self) -> set[str]:
        return set(self._rules)

    def require_identity(self, identity: str) -> None:
        if identity not in self._rules:
            raise PermissionError("Unknown or unbound client identity")

    def allows(self, identity: str, path: str, mode: str) -> bool:
        self.require_identity(identity)
        return any(fnmatchcase(path, pattern) for pattern in self._rules[identity][mode])

    def require(self, identity: str, path: str, mode: str) -> None:
        if not self.allows(identity, path, mode):
            raise PermissionError(f"Permission denied: {identity} cannot {mode} {path}")
