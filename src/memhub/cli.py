"""Explicitly configured service, identity administration, and fact import CLI."""

import argparse
from getpass import getpass
import json
from pathlib import Path
import re

from argon2 import PasswordHasher
import uvicorn

from memhub.config import load_config
from memhub.permissions import PermissionPolicy
from memhub.storage import MemoryStore, initialize_memory


def set_password(config) -> None:
    password = getpass("OAuth password: ")
    confirmation = getpass("Confirm OAuth password: ")
    if not password or password != confirmation:
        raise ValueError("Passwords must be nonempty and match")
    config.env_path.parent.mkdir(parents=True, exist_ok=True)
    config.env_path.touch(mode=0o600, exist_ok=True)
    config.env_path.chmod(0o600)
    lines = config.env_path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not re.match(r"^\s*(?:export\s+)?OAUTH_PASSWORD_HASH\s*=", line)]
    lines.append("OAUTH_PASSWORD_HASH='" + PasswordHasher().hash(password) + "'")
    config.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config.env_path.chmod(0o600)
    print("OAuth password hash saved")


def import_facts(config, source: str, path: str, text_file: Path, description: str | None) -> dict:
    """Import through the same storage rules, reporting errors without fact text."""
    result = {"written": 0, "duplicate": 0, "rejected": 0, "reasons": []}
    store = MemoryStore(config)
    try:
        for number, line in enumerate(text_file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            fact = re.sub(r"^\s*(?:[-*]\s+|\d+[.)、]\s*)", "", line).strip()
            try:
                if path == "profile.md":
                    raise ValueError("profile.md cannot be imported; use remember with explicit confirmation")
                item = store.remember(path, fact, source, description=description)
            except (ValueError, PermissionError) as error:
                result["rejected"] += 1
                result["reasons"].append({"line": number, "reason": str(error)})
            else:
                result[item["status"]] += 1
    finally:
        store.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="memhub")
    parser.add_argument("--config", type=Path, required=True, help="Required root parameter file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize an empty independent memory Git repository")
    commands.add_parser("serve", help="Run the HTTP MCP service")
    commands.add_parser("set-password", help="Set the single-user OAuth login password")
    clients = commands.add_parser("clients", help="Administer registered OAuth clients")
    client_commands = clients.add_subparsers(dest="client_command", required=True)
    client_commands.add_parser("list")
    revoke = client_commands.add_parser("revoke")
    revoke.add_argument("client_id")
    importer = commands.add_parser("import", help="Import one fact per nonempty text-file line")
    importer.add_argument("--source", required=True)
    importer.add_argument("--path", required=True)
    importer.add_argument("--description")
    importer.add_argument("text_file", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "init":
        initialize_memory(config)
        print(f"Initialized memory repository: {config.memory_dir}")
    elif args.command == "serve":
        from memhub.server import create_app

        uvicorn.run(create_app(config), host=config.host, port=config.port)
    elif args.command == "set-password":
        set_password(config)
    elif args.command == "import":
        print(json.dumps(import_facts(config, args.source, args.path, args.text_file, args.description), ensure_ascii=False))
    else:
        from memhub.auth import OAuthProvider

        provider = OAuthProvider(config, PermissionPolicy(config.clients_path))
        try:
            if args.client_command == "list":
                print(json.dumps(provider.clients_list(), ensure_ascii=False))
            else:
                provider.revoke_client(args.client_id)
                print(f"Revoked all tokens for OAuth client: {args.client_id}")
        finally:
            provider.close()
