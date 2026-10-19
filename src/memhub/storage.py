"""Markdown is authoritative; SQLite is a rebuildable fact search index."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from threading import RLock
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

import jieba
import yaml

from memhub.config import Config
from memhub.permissions import PermissionPolicy


_FACT_PATH = re.compile(r"(?:profile|preferences|_inbox)\.md|(?:topics|areas|people)/[^/\\\r\n\x00]+\.md")
# One append-only file per local day; kept out of the fact catalog and search index.
_JOURNAL_PATH = re.compile(r"journal/\d{4}-\d{2}-\d{2}\.md")
_SOURCE_SUFFIX = re.compile(r"\s*\[[^\]\r\n]*·[^\]\r\n]*\]\s*$")


def _today(config: Config) -> str:
    return datetime.now(ZoneInfo(config.timezone)).date().isoformat()


def _version(content: str) -> str:
    # This digest is only an optimistic concurrency token, never a checksum check.
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _git(config: Config, *arguments: str) -> None:
    subprocess.run(
        [
            "git", "-C", str(config.memory_dir),
            "-c", f"user.name={config.git_author_name}",
            "-c", f"user.email={config.git_author_email}",
            "-c", "commit.gpgsign=false", *arguments,
        ],
        check=True, capture_output=True, text=True,
    )


def _render(metadata: dict, body: list[str]) -> str:
    header = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip()
    return "---\n" + header + "\n---\n" + "".join(line + "\n" for line in body)


def initialize_memory(config: Config) -> None:
    """Create the independent memory repository, never replace an existing path."""
    if config.memory_dir.exists() and (not config.memory_dir.is_dir() or any(config.memory_dir.iterdir())):
        raise ValueError("memory_dir already exists; initialization refused")
    config.memory_dir.mkdir(parents=True, exist_ok=True)
    skeletons = {
        "profile.md": "身份与长期目标",
        "preferences.md": "回答方式与长期偏好",
        "_inbox.md": "尚未归类的长期事实",
    }
    for path, description in skeletons.items():
        content = _render(
            {"name": Path(path).stem, "description": description, "aliases": [], "updated": _today(config)},
            [],
        )
        if len(content.encode("utf-8")) > config.max_file_bytes:
            raise ValueError("Memory skeleton exceeds the configured file limit; increase the limit")
        (config.memory_dir / path).write_text(content, encoding="utf-8")
    _git(config, "init", "--initial-branch=main")
    _git(config, "add", "--", *skeletons)
    _git(config, "commit", "--only", "-m", "memhub init memory skeletons", "--", *skeletons)


class MemoryStore:
    def __init__(self, config: Config, permissions: PermissionPolicy | None = None):
        self.config = config
        self.permissions = permissions if permissions is not None else PermissionPolicy(config.clients_path)
        self._lock = RLock()
        self._sensitive = [re.compile(pattern) for pattern in config.sensitive_patterns]
        if not config.memory_dir.is_dir() or not (config.memory_dir / ".git").is_dir():
            raise ValueError("Memory repository is not initialized; run memhub --config <path> init first")
        config.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(config.index_path, check_same_thread=False)
        self._db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS facts USING fts5("
            "path UNINDEXED, line UNINDEXED, text UNINDEXED, terms, metadata, tokenize='unicode61')"
        )
        with self._lock, self._db:
            self._db.execute("DELETE FROM facts")
            for path in self._paths():
                self._index_file(path)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _path(self, path: str) -> Path:
        if not isinstance(path, str) or ".." in path or not (_FACT_PATH.fullmatch(path) or _JOURNAL_PATH.fullmatch(path)):
            raise ValueError("Invalid memory path; only the approved Markdown paths are allowed")
        target = self.config.memory_dir / path
        if not target.resolve().is_relative_to(self.config.memory_dir.resolve()):
            raise ValueError("Invalid memory path; symlink escapes are not allowed")
        return target

    def _paths(self) -> list[str]:
        paths = []
        for target in self.config.memory_dir.rglob("*.md"):
            path = target.relative_to(self.config.memory_dir).as_posix()
            if _FACT_PATH.fullmatch(path) and ".." not in path:
                self._path(path)
                if target.is_file():
                    paths.append(path)
        return sorted(paths)

    def _document(self, path: str) -> tuple[str, dict, list[str], int]:
        content = self._path(path).read_text(encoding="utf-8")
        lines = content.splitlines()
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            raise ValueError(f"Missing YAML front matter in {path}")
        end = lines.index("---", 1)
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(metadata, dict) or not {"name", "description", "aliases", "updated"} <= metadata.keys():
            raise ValueError(f"Required front matter fields are missing in {path}")
        if not isinstance(metadata["name"], str) or not isinstance(metadata["description"], str):
            raise ValueError(f"Invalid front matter in {path}")
        if not isinstance(metadata["aliases"], list) or not all(isinstance(alias, str) for alias in metadata["aliases"]):
            raise ValueError(f"Invalid aliases in {path}")
        body = lines[end + 1:]
        if any(line and (not line.startswith("- ") or not _SOURCE_SUFFIX.search(line)) for line in body):
            raise ValueError(f"Invalid fact line in {path}")
        return content, metadata, body, end + 2

    @staticmethod
    def _fact(line: str) -> str:
        return _SOURCE_SUFFIX.sub("", line[2:]).strip()

    @staticmethod
    def _terms(text: str) -> list[str]:
        # Strip punctuation before building MATCH expressions; quote all terms.
        return [part for token in jieba.cut_for_search(text) for part in re.findall(r"\w+", token)]

    def _index_file(self, path: str) -> None:
        _, metadata, body, first_line = self._document(path)
        terms_metadata = " ".join(self._terms(" ".join([metadata["name"], metadata["description"], *metadata["aliases"]])))
        self._db.execute("DELETE FROM facts WHERE path = ?", (path,))
        self._db.executemany(
            "INSERT INTO facts(path, line, text, terms, metadata) VALUES (?, ?, ?, ?, ?)",
            [
                (path, first_line + index, line, " ".join(self._terms(self._fact(line))), terms_metadata)
                for index, line in enumerate(body) if line
            ],
        )

    def _input(self, text: str, *, fact: bool = False) -> str:
        if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
            raise ValueError("Text must be a nonempty single line")
        if any(pattern.search(text) for pattern in self._sensitive):
            raise ValueError("Sensitive information is not permitted in memory")
        if fact and _SOURCE_SUFFIX.search(text):
            raise ValueError("Source suffixes must be supplied by the server")
        return text.strip()

    @staticmethod
    def _normalized(text: str) -> str:
        return "".join(character for character in unicodedata.normalize("NFKC", text).casefold() if character.isalnum())

    @staticmethod
    def _require_fact_path(path: str) -> None:
        if isinstance(path, str) and _JOURNAL_PATH.fullmatch(path):
            raise ValueError("Journal files are append-only; use log")

    def _write(self, path: str, metadata: dict, body: list[str], identity: str, tool: str) -> str:
        metadata["updated"] = _today(self.config)
        content = _render(metadata, body)
        if len(content.encode("utf-8")) > self.config.max_file_bytes:
            raise ValueError("Memory file exceeds the configured byte limit; split the facts into separate files")
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as temporary:
            temporary.write(content)
            temporary_path = temporary.name
        os.replace(temporary_path, target)
        _git(self.config, "add", "--", path)
        _git(self.config, "commit", "--only", "--allow-empty", "-m", f"{identity}: {tool} {path}", "--", path)
        if _FACT_PATH.fullmatch(path):
            with self._db:
                self._index_file(path)
        return _version(content)

    def get_context(self, identity: str | None = None) -> dict:
        with self._lock:
            if identity is not None:
                self.permissions.require_identity(identity)
            # Compact by design: path -> description catalog and bare fact lines.
            # Front matter, source suffixes and versions stay with read.
            files = {}
            for path in self._paths():
                if identity is not None and not self.permissions.allows(identity, path, "read"):
                    continue
                _, metadata, _, _ = self._document(path)
                aliases = f" (aliases: {', '.join(metadata['aliases'])})" if metadata["aliases"] else ""
                files[path] = metadata["description"] + aliases
            result = {
                "files": files,
            }
            for path in ("profile.md", "preferences.md"):
                if identity is None or self.permissions.allows(identity, path, "read"):
                    body = self._document(path)[2]
                    result[Path(path).stem] = "\n".join(_SOURCE_SUFFIX.sub("", line) for line in body if line)
            text = str(result)
            non_ascii = sum(ord(character) > 127 for character in text)
            estimated_tokens = non_ascii + math.ceil((len(text) - non_ascii) / 4)
            return {**result, "estimated_tokens": estimated_tokens, "token_budget": self.config.context_token_budget, "over_budget": estimated_tokens > self.config.context_token_budget}

    def read(self, paths: list[str], identity: str | None = None) -> dict:
        with self._lock:
            if identity is not None:
                self.permissions.require_identity(identity)
            if len(paths) > self.config.read_max_paths:
                raise ValueError(f"Too many paths requested ({len(paths)}); read at most "
                                 f"{self.config.read_max_paths} paths per call and split larger batches")
            files = []
            for path in paths:
                if identity is not None:
                    self.permissions.require(identity, path, "read")
                content = self._document(path)[0]
                files.append({"path": path, "content": content, "version": _version(content)})
            return {"files": files}

    def search(self, query: str, limit: int | None = None, identity: str | None = None) -> dict:
        with self._lock:
            if identity is not None:
                self.permissions.require_identity(identity)
            if limit is None:
                limit = self.config.search_default_limit
            if not isinstance(limit, int) or limit < 1:
                raise ValueError("Search limit must be a positive integer")
            terms = self._terms(query)
            if not terms:
                return {"hits": []}
            expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            visible_paths = self._paths() if identity is None else [
                path for path in self._paths() if self.permissions.allows(identity, path, "read")
            ]
            if not visible_paths:
                return {"hits": []}
            placeholders = ",".join("?" for _ in visible_paths)
            rows = self._db.execute(
                f"SELECT path, line, text FROM facts WHERE facts MATCH ? AND path IN ({placeholders}) "
                "ORDER BY rank, path, CAST(line AS INTEGER) LIMIT ?",
                (expression, *visible_paths, limit),
            ).fetchall()
            return {"hits": [{"path": path, "line": int(line), "text": text} for path, line, text in rows]}

    def remember(self, path: str, fact: str, identity: str, description: str | None = None, confirm: bool = False) -> dict:
        with self._lock:
            self._require_fact_path(path)
            self.permissions.require(identity, path, "write")
            target = self._path(path)
            fact = self._input(fact, fact=True)
            if description is not None:
                description = self._input(description)
            if path == "profile.md" and confirm is not True:
                raise ValueError("Writing profile.md requires confirm=true; first ask the user for consent")
            if target.exists():
                content, metadata, body, first_line = self._document(path)
                version = _version(content)
                candidates = [
                    {"path": path, "line": first_line + index, "text": line, "version": version}
                    for index, line in enumerate(body) if line and SequenceMatcher(
                        None, self._normalized(fact), self._normalized(self._fact(line))
                    ).ratio() >= self.config.duplicate_threshold
                ]
                if candidates:
                    if not self.permissions.allows(identity, path, "read"):
                        return {"status": "duplicate", "path": path,
                                "message": "Similar facts already exist; read permission is required to inspect candidates"}
                    return {"status": "duplicate", "candidates": candidates, "version": version, "message": "Similar facts already exist; use update to replace an existing fact"}
            else:
                if description is None:
                    raise ValueError("description is required when creating a new memory file")
                metadata = {"name": Path(path).stem, "description": description, "aliases": [], "updated": _today(self.config)}
                body = []
            body.append(f"- {fact} [{identity} · {_today(self.config)}]")
            return {"status": "written", "path": path, "version": self._write(path, metadata, body, identity, "remember")}

    def update(self, path: str, old_text: str, new_text: str, version: str, identity: str) -> dict:
        with self._lock:
            self._require_fact_path(path)
            self.permissions.require(identity, path, "write")
            new_text = self._input(new_text, fact=True)
            content, metadata, body, _ = self._document(path)
            current_version = _version(content)
            if version != current_version:
                if not self.permissions.allows(identity, path, "read"):
                    return {"status": "conflict", "version": current_version,
                            "message": "The version is stale; read permission is required to inspect current contents"}
                return {"status": "conflict", "content": content, "version": current_version}
            if not isinstance(old_text, str) or not old_text:
                raise ValueError("old_text must occur exactly once in fact text")
            matches = [(index, self._fact(line).count(old_text)) for index, line in enumerate(body) if line]
            if sum(count for _, count in matches) != 1:
                raise ValueError("old_text must occur exactly once in fact text")
            index = next(index for index, count in matches if count)
            body[index] = f"- {new_text} [{identity} · {_today(self.config)}]"
            return {"status": "written", "path": path, "version": self._write(path, metadata, body, identity, "update")}

    def forget(self, path: str, text: str, identity: str) -> dict:
        with self._lock:
            self._require_fact_path(path)
            self.permissions.require(identity, path, "write")
            _, metadata, body, _ = self._document(path)
            matches = [index for index, line in enumerate(body) if line and (text == line or text == self._fact(line))]
            if len(matches) != 1:
                raise ValueError("text must exactly match one fact line")
            del body[matches[0]]
            return {"status": "written", "path": path, "version": self._write(path, metadata, body, identity, "forget")}

    def log(self, entry: str, identity: str) -> dict:
        with self._lock:
            now = datetime.now(ZoneInfo(self.config.timezone))
            day = now.date().isoformat()
            path = f"journal/{day}.md"
            self.permissions.require(identity, path, "write")
            entry = self._input(entry, fact=True)
            if self._path(path).exists():
                _, metadata, body, _ = self._document(path)
            else:
                metadata = {"name": day, "description": f"{day} 各 Agent 的对话记录", "aliases": [], "updated": day}
                body = []
            body.append(f"- {entry} [{identity} · {now:%H:%M}]")
            return {"status": "written", "path": path, "version": self._write(path, metadata, body, identity, "log")}
