from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


class MaxGroupCreateError(Exception):
    pass


class MaxPhoneResolveError(Exception):
    pass


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _base_dir() -> Path:
    return Path(getattr(settings, "BASE_DIR"))


def _resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (_base_dir() / path).resolve()


def _run_command(command: list[str], timeout_sec: int) -> CommandResult:
    proc = subprocess.run(
        command,
        cwd=str(_base_dir()),
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )
    return CommandResult(returncode=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "")


def _extract_chat_id(output: str) -> str:
    match = re.search(r"Create chat OK:\s*chat_id=([^\s]+)", output)
    if not match:
        return ""
    return str(match.group(1)).strip()


def _extract_resolved_user_id(output: str) -> int:
    match = re.search(r"Resolve phone OK:\s*user_id=(\d+)", output)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def refresh_max_ws_token(profile_dir: str, token_file: str, timeout_sec: int) -> CommandResult:
    script_path = _base_dir() / "scripts" / "max_ws_token_keeper.py"
    profile_path = _resolve_path(profile_dir)
    token_path = _resolve_path(token_file)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(script_path),
        "--profile-dir",
        str(profile_path),
        "--refresh-only",
        "--save-env",
        "--env-file",
        str(token_path),
        "--timeout",
        str(timeout_sec),
    ]
    return _run_command(command, timeout_sec=max(60, timeout_sec + 30))


def create_max_group_via_ws(
    title: str,
    user_ids: list[int],
    token_file: str,
    timeout_sec: int = 30,
    bot_user_id: int = 0,
    bot_grant_admin: bool = False,
) -> dict:
    script_path = _base_dir() / "scripts" / "max_ws_create_group.py"
    token_path = _resolve_path(token_file)
    user_ids_raw = ",".join(str(x) for x in user_ids)

    command = [
        sys.executable,
        str(script_path),
        "--title",
        title,
        "--user-ids",
        user_ids_raw,
        "--token-file",
        str(token_path),
        "--timeout",
        str(timeout_sec),
    ]
    if int(bot_user_id or 0) > 0:
        command.extend(["--bot-user-id", str(int(bot_user_id))])
        if bot_grant_admin:
            command.append("--bot-admin")
    result = _run_command(command, timeout_sec=max(60, timeout_sec + 30))
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise MaxGroupCreateError(msg or "MAX group creation command failed")

    chat_id = _extract_chat_id(result.stdout)
    return {
        "ok": True,
        "chat_id": chat_id,
        "stdout": result.stdout,
    }


def resolve_max_user_id_via_ws(
    *,
    phone: str,
    token_file: str,
    timeout_sec: int = 20,
) -> int:
    script_path = _base_dir() / "scripts" / "max_ws_resolve_phone.py"
    token_path = _resolve_path(token_file)
    normalized_phone = str(phone or "").strip()
    if not normalized_phone:
        raise MaxPhoneResolveError("phone is empty")

    command = [
        sys.executable,
        str(script_path),
        "--phone",
        normalized_phone,
        "--token-file",
        str(token_path),
        "--timeout",
        str(timeout_sec),
    ]
    result = _run_command(command, timeout_sec=max(30, timeout_sec + 20))
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise MaxPhoneResolveError(msg or "MAX phone resolve command failed")

    resolved_user_id = _extract_resolved_user_id(result.stdout)
    if resolved_user_id <= 0:
        raise MaxPhoneResolveError(f"user_id not found in resolver output: {result.stdout.strip()}")
    return resolved_user_id
