# BrainDrive Bootstrap Installer

Cross-platform installer for BrainDrive local setup and non-Docker production-style setup.

Primary script:
- `scripts/bootstrap_braindrive.py`

Wrappers:
- `scripts/bootstrap.sh` (macOS/Linux)
- `scripts/bootstrap.ps1` (Windows PowerShell)

## What It Automates
1. Creates or reuses a conda environment with `python`, `nodejs`, and `git`.
2. Creates `backend/.env` from `backend/.env-dev` (if needed).
3. Creates `frontend/.env` from `frontend/.env.example` (if needed).
4. Generates secure backend secrets when placeholders are detected.
5. Applies mode-specific env values:
   - `dev`: split frontend/backend setup
   - `prod`: backend-served frontend assumptions (`SERVE_FRONTEND=true`, frontend build path, `/api` base)
6. Installs backend (`pip`) and frontend (`npm`) dependencies.
7. Builds frontend in `prod` mode unless skipped.

## Prerequisite
Conda (Miniconda/Anaconda) must be installed and available in `PATH`.

## Usage

### macOS/Linux
```bash
python3 scripts/bootstrap_braindrive.py --mode dev
```
or
```bash
bash scripts/bootstrap.sh --mode dev
```

### Windows PowerShell
```powershell
python scripts/bootstrap_braindrive.py --mode dev
```
or
```powershell
.\scripts\bootstrap.ps1 --mode dev
```

### Production-style (non-Docker)
```bash
python3 scripts/bootstrap_braindrive.py --mode prod
```

Optional domain example:
```bash
python3 scripts/bootstrap_braindrive.py --mode prod --domain https://brain.example.com
```

## Useful Flags
1. `--env-name BrainDriveDev`
2. `--skip-conda-create`
3. `--skip-backend-install`
4. `--skip-frontend-install`
5. `--skip-frontend-build`
6. `--overwrite-env-files`
7. `--dry-run`

Example dry run:
```bash
python3 scripts/bootstrap_braindrive.py --mode prod --dry-run
```

## Notes
1. The script uses `conda run -n <env>` to avoid shell-specific activation differences across OSes.
2. Service-runtime installs can still use backend-local `venv` when required; this script does not remove that pattern.
