#!/usr/bin/env python3
"""
BrainDrive cross-platform bootstrap installer.

Designed for macOS, Windows, and Linux with a conda-first baseline:
- Creates or reuses a conda environment with Python + Node.js + Git
- Prepares backend/frontend .env files
- Generates secure backend secrets when placeholders are detected
- Installs backend/frontend dependencies
- Optionally builds frontend for production-like non-Docker runtime

Examples:
  python scripts/bootstrap_braindrive.py --mode dev
  python scripts/bootstrap_braindrive.py --mode prod
  python scripts/bootstrap_braindrive.py --mode prod --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable


ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap BrainDrive with a conda-first workflow.")
    parser.add_argument("--mode", choices=["dev", "prod"], default="dev", help="Install mode.")
    parser.add_argument("--env-name", default="BrainDriveDev", help="Conda environment name.")
    parser.add_argument(
        "--python-version",
        default="3.11",
        help="Python version for conda env creation (default: 3.11).",
    )
    parser.add_argument("--backend-host", default=None, help="Backend host override.")
    parser.add_argument("--backend-port", type=int, default=8005, help="Backend port.")
    parser.add_argument("--frontend-port", type=int, default=5173, help="Frontend dev port.")
    parser.add_argument(
        "--domain",
        default="",
        help="Optional production domain for CORS_ORIGINS (example: https://brain.yourdomain.com).",
    )
    parser.add_argument("--skip-conda-create", action="store_true", help="Skip conda environment creation.")
    parser.add_argument("--skip-backend-install", action="store_true", help="Skip backend dependency install.")
    parser.add_argument("--skip-frontend-install", action="store_true", help="Skip frontend dependency install.")
    parser.add_argument("--skip-frontend-build", action="store_true", help="Skip frontend production build.")
    parser.add_argument("--overwrite-env-files", action="store_true", help="Overwrite existing .env files.")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without making changes.")
    return parser.parse_args()


def run_command(cmd: Iterable[str], cwd: Path | None = None, dry_run: bool = False) -> None:
    command_text = " ".join(cmd)
    prefix = "[dry-run]" if dry_run else "[run]"
    print(f"{prefix} {command_text}")
    if dry_run:
        return
    subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, check=True)


def resolve_repo_root() -> Path:
    # Supports script location in repo-root subfolders (for example: scripts/).
    script_path = Path(__file__).resolve()
    for candidate in [script_path.parent, *script_path.parents]:
        if (candidate / "backend").exists() and (candidate / "frontend").exists():
            return candidate
    raise RuntimeError("Could not resolve repo root from script location.")


def get_conda_executable() -> str:
    conda = shutil.which("conda")
    if not conda:
        raise RuntimeError("Conda was not found in PATH. Install Miniconda/Anaconda first.")
    return conda


def conda_env_exists(conda_exe: str, env_name: str) -> bool:
    result = subprocess.run(
        [conda_exe, "env", "list", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    for env_path in payload.get("envs", []):
        if Path(env_path).name == env_name:
            return True
    return False


def copy_if_needed(source: Path, target: Path, overwrite: bool, dry_run: bool) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Missing template file: {source}")
    if target.exists() and not overwrite:
        print(f"[skip] {target} already exists")
        return
    print(f"[copy] {source} -> {target}")
    if dry_run:
        return
    shutil.copyfile(source, target)


def parse_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ENV_LINE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        data[key] = value
    return data


def format_env_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def update_env_file(path: Path, updates: Dict[str, object], dry_run: bool) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = {k: format_env_value(v) for k, v in updates.items()}
    output_lines = list(lines)

    for idx, line in enumerate(output_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ENV_LINE_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in pending:
            output_lines[idx] = f"{key}={pending.pop(key)}"

    for key, value in pending.items():
        output_lines.append(f"{key}={value}")

    print(f"[update] {path}")
    if dry_run:
        return
    path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


def needs_generated_secret(value: str | None) -> bool:
    if not value:
        return True
    probe = value.strip().lower()
    return probe in {
        "your-secret-key-here",
        "your-encryption-master-key-here",
        "generate-a-secure-token-here",
    }


def generate_secret(length: int) -> str:
    # URL-safe token to avoid shell/INI escaping issues.
    token = secrets.token_urlsafe(length)
    return token[:length]


def build_backend_updates(
    mode: str,
    current_env: Dict[str, str],
    backend_host: str,
    backend_port: int,
    frontend_port: int,
    repo_root: Path,
    domain: str,
) -> Dict[str, object]:
    updates: Dict[str, object] = {
        "HOST": backend_host,
        "PORT": backend_port,
        "APP_ENV": "dev" if mode == "dev" else "prod",
        "DEBUG": mode == "dev",
        "RELOAD": False,
        "ENABLE_TEST_ROUTES": mode == "dev",
        "CORS_DEV_HOSTS": "localhost,127.0.0.1,[::1]",
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
    }

    if mode == "dev":
        updates["SERVE_FRONTEND"] = False
        updates["CORS_ORIGINS"] = f"http://127.0.0.1:{frontend_port},http://localhost:{frontend_port}"
    else:
        updates["SERVE_FRONTEND"] = True
        updates["FRONTEND_DIST_PATH"] = str((repo_root / "frontend" / "dist").resolve())
        if domain.strip():
            updates["CORS_ORIGINS"] = domain.strip()
            updates["ALLOWED_HOSTS"] = f"localhost,127.0.0.1,{domain.strip().replace('https://', '').replace('http://', '')}"
        else:
            updates["CORS_ORIGINS"] = ""

    if needs_generated_secret(current_env.get("SECRET_KEY")):
        updates["SECRET_KEY"] = generate_secret(64)
    if needs_generated_secret(current_env.get("ENCRYPTION_MASTER_KEY")):
        updates["ENCRYPTION_MASTER_KEY"] = generate_secret(64)

    return updates


def build_frontend_updates(mode: str, backend_port: int, frontend_port: int) -> Dict[str, object]:
    updates: Dict[str, object] = {
        "VITE_PORT": frontend_port,
        "VITE_DEV_AUTO_LOGIN": False,
    }
    if mode == "dev":
        updates["VITE_API_URL"] = f"http://localhost:{backend_port}"
    else:
        updates["VITE_API_URL"] = "/api"
        updates["VITE_PLUGIN_STUDIO_DEV_MODE"] = False
        updates["VITE_SHOW_EDITING_CONTROLS"] = False
    return updates


def print_next_steps(mode: str, env_name: str, backend_port: int, frontend_port: int) -> None:
    print("\nNext steps:")
    if mode == "dev":
        print(f"1) conda activate {env_name}")
        print(f"2) Terminal A: cd backend && uvicorn main:app --host localhost --port {backend_port}")
        print("3) Terminal B: cd frontend && npm run dev")
        print(f"4) Open http://localhost:{frontend_port}")
    else:
        print(f"1) conda activate {env_name}")
        print(f"2) cd backend && uvicorn main:app --host 0.0.0.0 --port {backend_port} --workers 4")
        print(f"3) Open http://localhost:{backend_port}")
        print(f"4) API docs: http://localhost:{backend_port}/api/v1/docs")
    print("\nNote: service-runtime installs can still create/use backend-local venvs when needed.")


def main() -> int:
    args = parse_args()
    repo_root = resolve_repo_root()
    conda_exe = get_conda_executable()

    backend_host = args.backend_host or ("localhost" if args.mode == "dev" else "0.0.0.0")
    backend_dir = repo_root / "backend"
    frontend_dir = repo_root / "frontend"

    backend_env_template = backend_dir / ".env-dev"
    frontend_env_template = frontend_dir / ".env.example"
    backend_env_file = backend_dir / ".env"
    frontend_env_file = frontend_dir / ".env"

    print("BrainDrive bootstrap")
    print(f"- Mode: {args.mode}")
    print(f"- Repo: {repo_root}")
    print(f"- Conda env: {args.env_name}")
    print(f"- Dry run: {args.dry_run}")

    if not args.skip_conda_create:
        if conda_env_exists(conda_exe, args.env_name):
            print(f"[skip] conda env '{args.env_name}' already exists")
        else:
            run_command(
                [
                    conda_exe,
                    "create",
                    "-n",
                    args.env_name,
                    "-c",
                    "conda-forge",
                    f"python={args.python_version}",
                    "nodejs",
                    "git",
                    "-y",
                ],
                cwd=repo_root,
                dry_run=args.dry_run,
            )

    copy_if_needed(backend_env_template, backend_env_file, args.overwrite_env_files, args.dry_run)
    copy_if_needed(frontend_env_template, frontend_env_file, args.overwrite_env_files, args.dry_run)

    backend_current = parse_env_file(backend_env_file)
    backend_updates = build_backend_updates(
        mode=args.mode,
        current_env=backend_current,
        backend_host=backend_host,
        backend_port=args.backend_port,
        frontend_port=args.frontend_port,
        repo_root=repo_root,
        domain=args.domain,
    )
    update_env_file(backend_env_file, backend_updates, args.dry_run)

    frontend_updates = build_frontend_updates(args.mode, args.backend_port, args.frontend_port)
    update_env_file(frontend_env_file, frontend_updates, args.dry_run)

    if not args.skip_backend_install:
        run_command(
            [conda_exe, "run", "-n", args.env_name, "python", "-m", "pip", "install", "--upgrade", "pip"],
            cwd=backend_dir,
            dry_run=args.dry_run,
        )
        run_command(
            [conda_exe, "run", "-n", args.env_name, "python", "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=backend_dir,
            dry_run=args.dry_run,
        )

    if not args.skip_frontend_install:
        run_command([conda_exe, "run", "-n", args.env_name, "npm", "install"], cwd=frontend_dir, dry_run=args.dry_run)

    should_build_frontend = args.mode == "prod" and not args.skip_frontend_build
    if should_build_frontend:
        run_command([conda_exe, "run", "-n", args.env_name, "npm", "run", "build"], cwd=frontend_dir, dry_run=args.dry_run)

    print_next_steps(args.mode, args.env_name, args.backend_port, args.frontend_port)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"\nError: command failed with exit code {error.returncode}", file=sys.stderr)
        raise SystemExit(error.returncode)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"\nError: {exc}", file=sys.stderr)
        raise SystemExit(1)
