#!/usr/bin/env python3
"""
Create user-scoped Library Service directory structure for local development.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,128}$")


def normalize_user_id(raw_user_id: str) -> str:
    normalized = str(raw_user_id).strip().replace("-", "")
    if not normalized:
        raise ValueError("user_id cannot be empty")
    if not USER_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"user_id '{raw_user_id}' contains invalid characters after normalization"
        )
    return normalized


def ensure_user_structure(library_root: Path, user_id: str) -> dict[str, object]:
    normalized_user_id = normalize_user_id(user_id)
    scoped_root = library_root / "users" / normalized_user_id

    created_paths: list[str] = []
    required_dirs = [
        scoped_root,
        scoped_root / "projects" / "active",
        scoped_root / "transcripts",
        scoped_root / "pulse",
        scoped_root / "docs",
    ]
    for directory in required_dirs:
        if not directory.exists():
            created_paths.append(directory.as_posix())
        directory.mkdir(parents=True, exist_ok=True)

    activity_log = scoped_root / "activity.log"
    if not activity_log.exists():
        activity_log.touch()
        created_paths.append(activity_log.as_posix())

    return {
        "requested_user_id": user_id,
        "normalized_user_id": normalized_user_id,
        "scoped_root": scoped_root.as_posix(),
        "created_paths": created_paths,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap user-scoped Library Service directories."
    )
    parser.add_argument(
        "--library-root",
        required=True,
        help="Base BRAINDRIVE_LIBRARY_PATH directory.",
    )
    parser.add_argument(
        "--user-id",
        dest="user_ids",
        action="append",
        required=True,
        help="User id to bootstrap (repeat for multiple users).",
    )
    return parser.parse_args()


def run(library_root: Path, user_ids: Iterable[str]) -> dict[str, object]:
    resolved_root = library_root.expanduser().resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)

    users = [ensure_user_structure(resolved_root, user_id) for user_id in user_ids]
    return {
        "library_root": resolved_root.as_posix(),
        "users": users,
    }


def main() -> int:
    args = parse_args()
    payload = run(Path(args.library_root), args.user_ids)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
