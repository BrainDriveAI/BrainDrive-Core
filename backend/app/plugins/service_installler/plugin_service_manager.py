import os
import re
import shlex
import zipfile
import tarfile
import tempfile
import aiohttp
import asyncio
import subprocess
from pathlib import Path
from typing import List, Dict, Union, Optional
import structlog
import shutil
import traceback
from dotenv import dotenv_values

from app.dto.plugin import PluginServiceRuntimeDTO
from app.plugins.service_installler.docker_manager import (
    build_and_start_docker_service,
    restart_docker_service,
    stop_docker_service,
)
from app.plugins.service_installler.python_manager import install_python_service
from .prerequisites import check_required_env_vars, convert_to_download_url

from app.plugins.repository import PluginRepository
from app.core.database import get_db
from app.plugins.service_installler.service_health_checker import wait_for_service_health

logger = structlog.get_logger()

SERVICES_RUNTIME_ENV_VAR = "BRAINDRIVE_SERVICES_RUNTIME_DIR"


def _resolve_services_runtime_dir() -> Path:
    override = str(os.environ.get(SERVICES_RUNTIME_ENV_VAR, "")).strip()
    if override:
        return Path(override).expanduser().resolve()
    # /backend/app/plugins/service_installler/plugin_service_manager.py -> /backend
    backend_root = Path(__file__).resolve().parents[3]
    return (backend_root / "services_runtime").resolve()


RUNTIME_BASE = _resolve_services_runtime_dir()

DEFAULT_PYTHON_BIN = os.environ.get("PYTHON_BIN", "python3.11")
DEFAULT_ENV_INHERIT = "minimal"
ENV_INHERIT_ALL = "all"
MINIMAL_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SHELL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
}
MINIMAL_ENV_KEYS_UPPER = {key.upper() for key in MINIMAL_ENV_KEYS}

_RUNTIME_KEY_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_runtime_key(value: str) -> str:
    cleaned = _RUNTIME_KEY_SANITIZER.sub("_", value.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "service"


def _runtime_key_from_source_url(source_url: Optional[str]) -> Optional[str]:
    if not source_url:
        return None
    cleaned = source_url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if "://" in cleaned:
        _, remainder = cleaned.split("://", 1)
        path = remainder.split("/", 1)[-1] if "/" in remainder else ""
    else:
        # Handle git@host:owner/repo or local paths.
        path = cleaned.split(":", 1)[-1]
    if not path:
        return None
    repo = path.split("/")[-1]
    return repo or None


def _resolve_runtime_dir(service: PluginServiceRuntimeDTO, plugin_slug: Optional[str] = None) -> Path:
    runtime_dir_key = getattr(service, "runtime_dir_key", None)
    if runtime_dir_key:
        explicit_dir = RUNTIME_BASE / _sanitize_runtime_key(runtime_dir_key)
        plugin_slug = plugin_slug or getattr(service, "plugin_slug", None)
        legacy_key = f"{plugin_slug}_{service.name}" if plugin_slug and service.name else None
        legacy_dir = (RUNTIME_BASE / legacy_key) if legacy_key else None
        if legacy_dir and legacy_dir.exists() and not explicit_dir.exists():
            return legacy_dir
        return explicit_dir

    plugin_slug = plugin_slug or getattr(service, "plugin_slug", None)
    legacy_key = f"{plugin_slug}_{service.name}" if plugin_slug and service.name else None
    derived_key = _runtime_key_from_source_url(service.source_url) or service.name or legacy_key or "service"

    shared_dir = RUNTIME_BASE / _sanitize_runtime_key(derived_key)
    legacy_dir = (RUNTIME_BASE / legacy_key) if legacy_key else None
    if legacy_dir and legacy_dir.exists() and not shared_dir.exists():
        return legacy_dir
    return shared_dir


def _ensure_env_file(repo_dir: Path) -> None:
    env_path = repo_dir / ".env"
    if env_path.exists():
        return
    candidates = [
        repo_dir / ".env.local",
        repo_dir / ".env.local.example",
        repo_dir / ".env.example",
    ]
    for candidate in candidates:
        if candidate.exists():
            env_path.write_text(candidate.read_text())
            logger.info("Created .env from template", src=str(candidate), dest=str(env_path))
            return


def _parse_command(command: Optional[Union[str, List[str]]]) -> List[str]:
    if not command:
        return []
    if isinstance(command, (list, tuple)):
        return [str(part) for part in command if str(part).strip()]
    return shlex.split(str(command))


def _venv_python(repo_dir: Path) -> Optional[str]:
    if os.name == "nt":
        candidate = repo_dir / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = repo_dir / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return None


def _prefer_venv_python(cmd: List[str], repo_dir: Path) -> List[str]:
    if not cmd:
        return cmd
    venv_py = _venv_python(repo_dir)
    if not venv_py:
        return cmd
    if "python" in Path(cmd[0]).name.lower():
        return [venv_py, *cmd[1:]]
    return cmd


def _select_python_bin(service: PluginServiceRuntimeDTO) -> str:
    for candidate in (
        getattr(service, "start_command", None),
        getattr(service, "install_command", None),
        getattr(service, "stop_command", None),
        getattr(service, "restart_command", None),
    ):
        parsed = _parse_command(candidate)
        if parsed:
            return parsed[0]
    return DEFAULT_PYTHON_BIN


def _resolve_env_inherit(service: PluginServiceRuntimeDTO) -> str:
    inherit = getattr(service, "env_inherit", None) or os.environ.get("BRAINDRIVE_SERVICE_ENV_INHERIT")
    inherit = (inherit or DEFAULT_ENV_INHERIT).strip().lower()
    return ENV_INHERIT_ALL if inherit == ENV_INHERIT_ALL else DEFAULT_ENV_INHERIT


def _build_service_env(service: PluginServiceRuntimeDTO) -> Dict[str, str]:
    overrides = getattr(service, "env_overrides", None)
    if not isinstance(overrides, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in overrides.items()}


def _merge_env(overrides: Optional[Dict[str, str]], inherit: str) -> Dict[str, str]:
    if inherit == ENV_INHERIT_ALL:
        base = dict(os.environ)
    else:
        base = {key: value for key, value in os.environ.items() if key.upper() in MINIMAL_ENV_KEYS_UPPER}
    if overrides:
        base.update({str(k): "" if v is None else str(v) for k, v in overrides.items()})
    return base


def _resolve_venv_command(service: PluginServiceRuntimeDTO, action: str, repo_dir: Path) -> List[str]:
    attr_map = {
        "install": "install_command",
        "start": "start_command",
        "stop": "stop_command",
        "restart": "restart_command",
    }
    explicit = getattr(service, attr_map.get(action, ""), None)
    parsed = _parse_command(explicit)
    if parsed:
        return _prefer_venv_python(parsed, repo_dir)

    script_name = {
        "install": "install_with_venv.py",
        "start": "start_with_venv.py",
        "stop": "shutdown_with_venv.py",
        "restart": "restart_with_venv.py",
    }.get(action)
    if not script_name:
        return []
    script_rel = Path("service_scripts") / script_name
    if not (repo_dir / script_rel).exists():
        return []
    python_bin = _venv_python(repo_dir) or _select_python_bin(service)
    return [python_bin, str(script_rel)]

def ensure_service_dto(
    service_data: Union[Dict, PluginServiceRuntimeDTO], 
    plugin_id: str = None, 
    plugin_slug: str = None, 
    user_id: str = None
) -> PluginServiceRuntimeDTO:
    """
    Ensures the service data is a DTO, converting from dict if necessary.
    """
    return PluginServiceRuntimeDTO.from_dict_or_dto(service_data, plugin_id, plugin_slug, user_id)


async def install_and_run_required_services(
    services_runtime: list[dict],
    plugin_slug: str,
    plugin_id: str = None,
    user_id: str = None
):
    """
    Install and run required backend services for a plugin.
    Each service runs in its own venv and process.
    
    Args:
        services_runtime: List of service configurations (dicts from GitHub or DTOs from DB)
        plugin_slug: The plugin identifier slug
        plugin_id: Plugin ID (required when services_runtime contains dicts)
        user_id: User ID (required when services_runtime contains dicts)
    """
    base_services_dir = RUNTIME_BASE
    base_services_dir.mkdir(parents=True, exist_ok=True)

    # Define the path to the root .env file
    root_env_path = Path(os.getcwd()) / ".env"
    
    # Load the environment variables from the root .env file once
    env_vars = dotenv_values(root_env_path)
    
    # Configure session with longer timeouts and connection limits
    timeout = aiohttp.ClientTimeout(
        total=300,  # 5 minutes total
        connect=30,  # 30 seconds to connect
        sock_read=60  # 60 seconds for reading data
    )
    
    connector = aiohttp.TCPConnector(
        limit=10,
        limit_per_host=5,
        ttl_dns_cache=300,
        use_dns_cache=True
    )

    logger.info("Starting installation of required services")
    
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            for service in services_runtime:
                # Convert to DTO if it's a dict (first install from GitHub)
                service_dto = ensure_service_dto(service, plugin_id, plugin_slug, user_id)

                name = service_dto.name
                source_url = service_dto.source_url
                service_type = service_dto.type
                required_vars = service_dto.required_env_vars
                
                target_dir = _resolve_runtime_dir(service_dto, plugin_slug)
                
                # Download and extract repository
                if target_dir.exists():
                    logger.info(f"Service directory already exists: {target_dir}, skipping download.")
                else:
                    try:
                        await download_and_extract_repo(session, source_url, target_dir)
                        logger.info(f"Successfully downloaded and extracted {source_url} to {target_dir}")
                    except Exception as e:
                        logger.error(f"Failed to download repository {source_url}: {e}")
                        raise RuntimeError(f"Failed to download repository for service {name}: {e}")
                
                # Dispatch to the appropriate installer based on service type
                if service_type == 'python':
                    await install_python_service(service, target_dir)
                elif service_type == 'venv_process':
                    await _start_venv_service(service_dto)
                elif service_type == 'docker-compose':
                    await build_and_start_docker_service(service_dto, target_dir, env_vars, required_vars)
                else:
                    raise ValueError(f"Unknown service type: {service_type}")
    except Exception as e:
        logger.error(
            f"Installation failed: {type(e).__name__}: {e}\n"
            f"Traceback:\n{traceback.format_exc()}"
        )
        raise


async def download_and_extract_repo(session: aiohttp.ClientSession, source_url: str, target_dir: Path, max_retries: int = 3):
    """
    Download and extract a repository from a git URL.
    """
    download_url = convert_to_download_url(source_url)
    
    for attempt in range(max_retries):
        try:
            logger.info("Attempting to download repository", url=download_url, attempt=attempt + 1)
            async with session.get(download_url) as response:
                response.raise_for_status() # Raises for 4xx/5xx responses
                
                content_type = response.headers.get('content-type', '').lower()
                is_zip = 'zip' in content_type or download_url.endswith('.zip')
                
                with tempfile.NamedTemporaryFile(suffix='.zip' if is_zip else '.tar.gz', delete=False) as temp_file:
                    temp_path = Path(temp_file.name)
                    async for chunk in response.content.iter_chunked(16384):
                        temp_file.write(chunk)
                
                await _extract_archive(temp_path, target_dir, is_zip)
                return
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Download failed, retrying...", error=str(e), attempt=attempt + 1)
            await asyncio.sleep(2 ** attempt)
        finally:
            # Clean up temp file
            if 'temp_path' in locals() and temp_path.exists():
                temp_path.unlink(missing_ok=True)
                
    raise RuntimeError(f"Failed to download repository from {source_url} after {max_retries} attempts.")


async def _extract_archive(temp_path: Path, target_dir: Path, is_zip: bool):
    """
    Extracts a zip or tar.gz archive.
    """
    logger.info("Extracting archive", path=str(temp_path), target=str(target_dir))
    
    # Ensure target directory parent exists
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    
    # Create a temporary extraction directory
    temp_extract_dir = target_dir.parent / f"temp_extract_{target_dir.name}"
    if temp_extract_dir.exists():
        shutil.rmtree(temp_extract_dir)
    temp_extract_dir.mkdir(exist_ok=True)
    
    try:
        if is_zip:
            with zipfile.ZipFile(temp_path, 'r') as zip_file:
                zip_file.extractall(temp_extract_dir)
                # Find the first directory (e.g., repo-main)
                extracted_items = list(temp_extract_dir.iterdir())
                if len(extracted_items) == 1 and extracted_items[0].is_dir():
                    # Single directory case - move its contents
                    extracted_dir = extracted_items[0]
                    if target_dir.exists():
                        shutil.rmtree(target_dir)
                    shutil.move(str(extracted_dir), str(target_dir))
                else:
                    # Multiple items or files - move temp_extract_dir to target
                    if target_dir.exists():
                        shutil.rmtree(target_dir)
                    shutil.move(str(temp_extract_dir), str(target_dir))
                    temp_extract_dir = None  # Prevent cleanup since it's been moved
        else:
            with tarfile.open(temp_path, 'r:gz') as tar_file:
                tar_file.extractall(temp_extract_dir)
                # Find the first directory
                extracted_items = list(temp_extract_dir.iterdir())
                if len(extracted_items) == 1 and extracted_items[0].is_dir():
                    # Single directory case - move its contents
                    extracted_dir = extracted_items[0]
                    if target_dir.exists():
                        shutil.rmtree(target_dir)
                    shutil.move(str(extracted_dir), str(target_dir))
                else:
                    # Multiple items or files - move temp_extract_dir to target
                    if target_dir.exists():
                        shutil.rmtree(target_dir)
                    shutil.move(str(temp_extract_dir), str(target_dir))
                    temp_extract_dir = None  # Prevent cleanup since it's been moved
                
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        logger.error("Archive extraction failed", error=str(e))
        raise RuntimeError(f"Failed to extract archive: {e}")
    finally:
        # Clean up temp extraction directory if it still exists
        if temp_extract_dir and temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)


async def install_plugin_service(service_data: PluginServiceRuntimeDTO, plugin_slug: str):
    """
    Installs a single plugin service, including downloading the source
    and starting the service. This function is for first-time installation.
    """
    base_services_dir = RUNTIME_BASE
    base_services_dir.mkdir(parents=True, exist_ok=True)
    target_dir = _resolve_runtime_dir(service_data, plugin_slug)
    
    if target_dir.exists():
        logger.info("Service directory already exists, skipping installation", path=str(target_dir))
    else:
        # Download and extract the service source code
        env_vars = dotenv_values(Path(os.getcwd()) / ".env")
        timeout = aiohttp.ClientTimeout(total=300)
        connector = aiohttp.TCPConnector(limit=10)
        
        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                await download_and_extract_repo(session, service_data.source_url, target_dir)
        except Exception as e:
            logger.error("Failed to download or extract repository", error=str(e))
            raise RuntimeError(f"Failed to download repository for service {service_data.name}: {e}")

    # Dispatch to the appropriate installer/runner
    service_type = service_data.type or "docker-compose"
    required_vars = service_data.required_env_vars or []
    
    # Prerequisite Check
    # check_required_env_vars(
    #     service_name=service_data.name,
    #     required_vars=required_vars,
    #     root_env_path=Path(os.getcwd()) / ".env"
    # )
    
    if service_type == 'python':
        await install_python_service(service_data, target_dir)
    elif service_type == 'venv_process':
        await _start_venv_service(service_data)
    elif service_type == 'docker-compose':
        await build_and_start_docker_service(service_data, target_dir, dotenv_values(Path(os.getcwd()) / ".env"), required_vars)
    else:
        raise ValueError(f"Unknown service type: {service_type}")


async def start_plugin_services(services_runtime: List[PluginServiceRuntimeDTO], plugin_slug: str):
    """
    Starts a list of plugin services. This is used on application startup
    and assumes the code is already downloaded.
    """
    logger.info("Starting required plugin services")
    
    for service_data in services_runtime:
        target_dir = _resolve_runtime_dir(service_data, plugin_slug)
        service_type = service_data.type or "docker-compose"
        
        try:
            logger.info("Attempting to start service", name=service_data.name)
            if service_type == 'docker-compose':
                # The start_command is the same as the install command for docker
                await build_and_start_docker_service(
                    service_data,
                    target_dir,
                    dotenv_values(Path(os.getcwd()) / ".env"),
                    service_data.required_env_vars or []
                )
            elif service_type == 'python':
                # Assuming install_python_service can handle a pre-existing venv
                await install_python_service(service_data, target_dir)
            elif service_type == 'venv_process':
                await _start_venv_service(service_data)
            else:
                logger.warning("Skipping unknown service type", type=service_type, name=service_data.name)
                
        except Exception as e:
            logger.error("Failed to start service", name=service_data.name, error=str(e))
            # Continue to the next service even if one fails
            continue


async def stop_plugin_services(services_runtime: List[PluginServiceRuntimeDTO], plugin_slug: str):
    """
    Stops a list of plugin services. This is used on application shotdown.
    """
    logger.info("Stopping required plugin services")
    for service_data in services_runtime:
        target_dir = _resolve_runtime_dir(service_data, plugin_slug)
        service_type = service_data.type or "docker-compose"
        
        try:
            logger.info("Attempting to stop service", name=service_data.name)
            if service_type == 'docker-compose':
                # The start_command is the same as the install command for docker
                await stop_docker_service(
                    service_data,
                    target_dir,
                )
            elif service_type == 'venv_process':
                await _stop_venv_service(service_data)
            else:
                logger.warning("Skipping unknown service type", type=service_type, name=service_data.name)
                
        except Exception as e:
            logger.error("Failed to stop service", name=service_data.name, error=str(e))
            # Continue to the next service even if one fails
            continue


async def restart_plugin_services(plugin_slug: str, definition_id: str, user_id: str = None, service_name: str = None):
    """
    Restart one or all services for a given plugin, using env vars from DB (not .env file).
    """
    async for db in get_db():
        repo = PluginRepository(db)

        # Get plugin id
        plugin_data = await repo.get_plugin_by_slug(plugin_slug, user_id)

        if not plugin_data:
            # Raise a standard Python exception instead of HTTPException
            raise ValueError(f"Plugin {plugin_slug} not found")

        # Extract plugin ID
        plugin_id = plugin_data["id"]

        # Get all service runtimes for this plugin
        service_runtimes = await repo.get_service_runtimes_by_plugin_id(plugin_id)
        if not service_runtimes:
            raise RuntimeError(f"No services found for plugin {plugin_id}")

        # Get environment variables for this plugin's settings instance
        env_vars = await repo.get_settings_env_vars(definition_id, user_id) or {}

        results = {}
        for service_runtime in service_runtimes:
            if service_name and service_runtime.name != service_name:
                continue

            target_dir = _resolve_runtime_dir(service_runtime, plugin_slug)
            service_type = service_runtime.type or "docker-compose"

            try:
                # stop first
                if service_type == "docker-compose":
                    logger.info(f"Stopping existing Docker service: {service_runtime.name}")
                    await stop_docker_service(service_runtime, target_dir)
                    await restart_docker_service(
                        service_runtime,
                        target_dir,
                        env_vars,
                        service_runtime.required_env_vars or []
                    )
                elif service_type == "python":
                    # implement restart logic for python service
                    # (stop old process if tracked, then install/start again)
                    await install_python_service(service_runtime, target_dir)
                elif service_type == "venv_process":
                    await _restart_venv_service(service_runtime)
                results[service_runtime.name] = "restarted"
            except Exception as e:
                results[service_runtime.name] = f"failed: {str(e)}"

        return results


async def start_plugin_services_from_db(plugin_slug: str, definition_id: str, user_id: str = None, service_name: str = None):
    """
    Start one or all services for a given plugin, using env vars from DB.
    """
    async for db in get_db():
        repo = PluginRepository(db)

        plugin_data = await repo.get_plugin_by_slug(plugin_slug, user_id)
        if not plugin_data:
            raise ValueError(f"Plugin {plugin_slug} not found")

        plugin_id = plugin_data["id"]
        service_runtimes = await repo.get_service_runtimes_by_plugin_id(plugin_id)
        if not service_runtimes:
            raise RuntimeError(f"No services found for plugin {plugin_id}")

        env_vars = await repo.get_settings_env_vars(definition_id, user_id) or {}

        results: Dict[str, str] = {}
        for service_runtime in service_runtimes:
            if service_name and service_runtime.name != service_name:
                continue

            target_dir = _resolve_runtime_dir(service_runtime, plugin_slug)
            service_type = service_runtime.type or "docker-compose"

            try:
                if service_type == "docker-compose":
                    logger.info(f"Starting Docker service: {service_runtime.name}")
                    await restart_docker_service(
                        service_runtime,
                        target_dir,
                        env_vars,
                        service_runtime.required_env_vars or []
                    )
                elif service_type == "python":
                    await install_python_service(service_runtime, target_dir)
                elif service_type == "venv_process":
                    await _start_venv_service(service_runtime)
                else:
                    logger.warning("Skipping unknown service type", type=service_type, name=service_runtime.name)
                    results[service_runtime.name] = "skipped"
                    continue

                results[service_runtime.name] = "started"
            except Exception as e:
                results[service_runtime.name] = f"failed: {str(e)}"

        return results


async def stop_plugin_services_from_db(plugin_slug: str, definition_id: str, user_id: str = None, service_name: str = None):
    """
    Stop one or all services for a given plugin.
    """
    async for db in get_db():
        repo = PluginRepository(db)

        plugin_data = await repo.get_plugin_by_slug(plugin_slug, user_id)
        if not plugin_data:
            raise ValueError(f"Plugin {plugin_slug} not found")

        plugin_id = plugin_data["id"]
        service_runtimes = await repo.get_service_runtimes_by_plugin_id(plugin_id)
        if not service_runtimes:
            raise RuntimeError(f"No services found for plugin {plugin_id}")

        results: Dict[str, str] = {}
        for service_runtime in service_runtimes:
            if service_name and service_runtime.name != service_name:
                continue

            target_dir = _resolve_runtime_dir(service_runtime, plugin_slug)
            service_type = service_runtime.type or "docker-compose"

            try:
                if service_type == "docker-compose":
                    logger.info(f"Stopping Docker service: {service_runtime.name}")
                    await stop_docker_service(service_runtime, target_dir)
                elif service_type == "venv_process":
                    await _stop_venv_service(service_runtime)
                else:
                    logger.warning("Skipping unknown service type", type=service_type, name=service_runtime.name)
                    results[service_runtime.name] = "skipped"
                    continue

                results[service_runtime.name] = "stopped"
            except Exception as e:
                results[service_runtime.name] = f"failed: {str(e)}"

        return results


def _truncate_output(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} bytes]"


async def _monitor_process(proc: asyncio.subprocess.Process, cmd: List[str], log_file: Path) -> None:
    rc = await proc.wait()
    try:
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{cmd}] exited with rc={rc}\n")
    except Exception:
        pass


async def _run_subprocess(
    cmd: List[str],
    cwd: Path,
    timeout: int = 120,
    env: Optional[Dict[str, str]] = None,
    env_inherit: str = DEFAULT_ENV_INHERIT,
    wait: bool = True,
) -> None:
    """
    Run a subprocess and append stdout/stderr to the service_runtime.log in cwd for visibility.
    When wait=False the process is started in the background with output redirected to the log file.
    """
    log_file = cwd / "service_runtime.log"
    merged_env = _merge_env(env, env_inherit)

    if not wait:
        # Fire-and-forget with output redirected to the log file.
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{cmd}] starting in background\n")
        log_handle = log_file.open("ab")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=merged_env,
            stdout=log_handle,
            stderr=log_handle,
        )
        log_handle.close()
        asyncio.create_task(_monitor_process(proc, cmd, log_file))
        logger.info("Started background subprocess", cmd=" ".join(cmd), pid=proc.pid)
        return

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=merged_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        try:
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(f"\n[{cmd}] timed out after {timeout}s\n")
        except Exception:
            pass
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(cmd)}")

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    # Always append output to the service log for troubleshooting
    try:
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{cmd}] rc={proc.returncode}\n")
            if out:
                fh.write(out)
            if err:
                fh.write("\n-- stderr --\n")
                fh.write(err)
    except Exception:
        pass

    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\nstdout: {_truncate_output(out)}\nstderr: {_truncate_output(err)}"
        )
    if out or err:
        logger.info("Subprocess output", cmd=" ".join(cmd), stdout=_truncate_output(out), stderr=_truncate_output(err))


async def _start_venv_service(service: PluginServiceRuntimeDTO) -> None:
    repo_dir = _resolve_runtime_dir(service, getattr(service, "plugin_slug", None))
    await _ensure_venv_repo(repo_dir, service)
    _ensure_env_file(repo_dir)

    health_url = service.healthcheck_url
    if health_url:
        try:
            if await wait_for_service_health(health_url, timeout=3):
                logger.info("Service already healthy; skipping start", name=service.name, url=health_url)
                return
        except Exception:
            # proceed to start
            pass

    install_cmd = _resolve_venv_command(service, "install", repo_dir)
    if install_cmd:
        venv_dir = repo_dir / ".venv"
        if not venv_dir.exists():
            logger.info("Venv missing; running install command", name=service.name)
            await _run_subprocess(
                install_cmd,
                cwd=repo_dir,
                timeout=300,
                env=_build_service_env(service),
                env_inherit=_resolve_env_inherit(service),
            )

    start_cmd = _resolve_venv_command(service, "start", repo_dir)
    if not start_cmd:
        logger.warning("No start command available for venv service", name=service.name)
        return
    logger.info("Starting venv service", name=service.name, cmd=" ".join(start_cmd))
    await _run_subprocess(
        start_cmd,
        cwd=repo_dir,
        timeout=180,
        env=_build_service_env(service),
        env_inherit=_resolve_env_inherit(service),
        wait=False,
    )


async def _stop_venv_service(service: PluginServiceRuntimeDTO) -> None:
    repo_dir = _resolve_runtime_dir(service, getattr(service, "plugin_slug", None))
    if not repo_dir.exists():
        logger.warning("Venv service repo missing; skipping stop", name=service.name, path=str(repo_dir))
        return
    stop_cmd = _resolve_venv_command(service, "stop", repo_dir)
    if not stop_cmd:
        logger.warning("No stop command for venv service", name=service.name)
        return
    logger.info("Stopping venv service", name=service.name, cmd=" ".join(stop_cmd))
    try:
        await _run_subprocess(
            stop_cmd,
            cwd=repo_dir,
            timeout=60,
            env=_build_service_env(service),
            env_inherit=_resolve_env_inherit(service),
        )
    except Exception as e:
        logger.warning("Stop command failed", name=service.name, error=str(e))


async def _restart_venv_service(service: PluginServiceRuntimeDTO) -> None:
    repo_dir = _resolve_runtime_dir(service, getattr(service, "plugin_slug", None))
    if not repo_dir.exists():
        logger.info("Venv service repo missing; starting fresh", name=service.name, path=str(repo_dir))
        await _start_venv_service(service)
        return
    restart_cmd = _resolve_venv_command(service, "restart", repo_dir)
    if restart_cmd:
        logger.info("Restarting venv service", name=service.name, cmd=" ".join(restart_cmd))
        await _run_subprocess(
            restart_cmd,
            cwd=repo_dir,
            timeout=180,
            env=_build_service_env(service),
            env_inherit=_resolve_env_inherit(service),
            wait=False,
        )
        return
    # fallback: stop then start
    await _stop_venv_service(service)
    await _start_venv_service(service)


async def _ensure_venv_repo(repo_dir: Path, service: PluginServiceRuntimeDTO) -> None:
    if repo_dir.exists():
        return
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    repo_url = service.source_url
    if not repo_url:
        logger.warning("No repo_url provided for venv service", name=service.name)
        return
    logger.info("Cloning venv service repo", name=service.name, url=repo_url, dest=str(repo_dir))
    proc = await asyncio.create_subprocess_exec(
        "git",
        "clone",
        repo_url,
        str(repo_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to clone {repo_url}: {stderr.decode()}")
