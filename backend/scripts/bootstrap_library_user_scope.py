#!/usr/bin/env python3
"""
Create canonical user-scoped Library Service directory structure for local development.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.user_initializer.library_template import (  # noqa: E402
    apply_canonical_schema,
    copy_base_template_idempotent,
    resolve_base_template_path,
)

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


def ensure_user_structure(
    library_root: Path,
    user_id: str,
    template_root: Path,
) -> dict[str, object]:
    normalized_user_id = normalize_user_id(user_id)
    scoped_root = library_root / "users" / normalized_user_id
    scoped_root.mkdir(parents=True, exist_ok=True)

    copy_result = copy_base_template_idempotent(template_root, scoped_root)
    schema_result = apply_canonical_schema(scoped_root)

    changed_paths = sorted(
        {
            *copy_result.copied_files,
            *(path.as_posix() for path in getattr(schema_result, "changed_paths", []) or []),
        }
    )

    return {
        "requested_user_id": user_id,
        "normalized_user_id": normalized_user_id,
        "scoped_root": scoped_root.as_posix(),
        "copied_template_files": list(copy_result.copied_files),
        "skipped_template_files": list(copy_result.skipped_files),
        "changed_paths": changed_paths,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap canonical user-scoped Library Service directories."
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
    parser.add_argument(
        "--template-root",
        required=False,
        help="Optional Base_Library template path override.",
    )
    return parser.parse_args()


def run(
    library_root: Path,
    user_ids: Iterable[str],
    template_root: Path | None = None,
) -> dict[str, object]:
    resolved_root = library_root.expanduser().resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)

    resolved_template = (
        template_root.expanduser().resolve()
        if isinstance(template_root, Path)
        else resolve_base_template_path()
    )
    if not resolved_template.is_dir():
        raise FileNotFoundError(f"Template root does not exist: {resolved_template}")

    users = [
        ensure_user_structure(resolved_root, user_id, resolved_template)
        for user_id in user_ids
    ]
    return {
        "library_root": resolved_root.as_posix(),
        "template_root": resolved_template.as_posix(),
        "users": users,
    }


def main() -> int:
    args = parse_args()
    template_root = Path(args.template_root) if args.template_root else None
    payload = run(Path(args.library_root), args.user_ids, template_root=template_root)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
