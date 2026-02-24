import subprocess
import asyncio
from pathlib import Path
from typing import Dict, List
import structlog
from app.plugins.service_installler.prerequisites import write_env_file
from app.plugins.service_installler.service_health_checker import wait_for_service_health
from app.dto.plugin import PluginServiceRuntimeDTO

logger = structlog.get_logger()

async def _run_docker_compose_command(command: str, cwd: Path):
    """
    Run a Docker Compose command and log its output.
    Uses asyncio.to_thread for non-blocking I/O.
    """
    import shlex
    logger.info("Executing Docker Compose command", command=command, cwd=str(cwd))

    def _execute():
        # Execute the command in the specified directory (no shell=True for safety)
        return subprocess.run(
            shlex.split(command),
            shell=False,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False
        )

    try:
        result = await asyncio.to_thread(_execute)
        if result.returncode != 0:
            logger.error(
                "Docker Compose command failed",
                command=command,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            raise RuntimeError(f"Docker Compose failed with error:\n{result.stderr}")
        
        logger.info(
            "Docker Compose command completed successfully",
            command=command,
            stdout=result.stdout
        )
    except Exception as e:
        logger.error("Failed to run Docker Compose command", command=command, error=str(e))
        raise RuntimeError(f"Failed to run Docker Compose command: {e}")


async def check_docker_availability():
    """
    Performs a comprehensive check to ensure Docker and Docker Compose are
    installed and the daemon is running.
    """
    logger.info("Checking Docker availability...")

    def _check():
        try:
            # Check docker command
            subprocess.run(["docker", "--version"], check=True, capture_output=True, text=True)
            # Check docker compose command
            subprocess.run(["docker", "compose", "version"], check=True, capture_output=True, text=True)
            # Check if docker daemon is running
            subprocess.run(["docker", "info"], check=True, capture_output=True, text=True, timeout=15)
            return True, "Docker and Docker Compose are available and the daemon is running."
        except subprocess.CalledProcessError as e:
            return False, f"Docker check failed: Command '{e.cmd}' returned non-zero exit code {e.returncode}. Stderr: {e.stderr}"
        except FileNotFoundError:
            return False, "Docker or Docker Compose command not found. Please ensure they are installed and in your system's PATH."
        except subprocess.TimeoutExpired:
            return False, "Docker daemon did not respond in time. It might not be running or is unresponsive."
        except Exception as e:
            return False, f"An unexpected error occurred during Docker check: {str(e)}"

    is_available, message = await asyncio.to_thread(_check)
    if not is_available:
        logger.error("Docker availability check failed", reason=message)
        raise RuntimeError(
            f"Docker is not available: {message}\n\n"
            "Please ensure:\n"
            "1. Docker Desktop is installed and running.\n"
            "2. Docker commands work from your terminal."
        )
    logger.info(message)


async def build_and_start_docker_service(
    service_data: PluginServiceRuntimeDTO,
    target_dir: Path,
    env_vars: Dict[str, str],
    required_vars: List[str]
):
    """
    Handles the installation and startup of a Docker Compose-based service.
    This includes checking Docker availability, writing env files, and running the service.
    """
    logger.info("Starting Docker service installation process", name=service_data.name)

    await check_docker_availability()

    install_command = service_data.install_command
    start_command = service_data.start_command
    healthcheck_url = service_data.healthcheck_url

    if not install_command:
        raise ValueError("Missing 'install_command' for Docker service.")
    
    if not start_command:
        raise ValueError("Missing 'start_command' for Docker service.")

    # Write environment file
    write_env_file(target_dir, env_vars, required_vars)

    # Clean up previous containers to avoid name conflicts
    await _run_docker_compose_command("docker compose down --remove-orphans", target_dir)

    # Rnu the docker compose build command
    await _run_docker_compose_command(install_command, target_dir)

    # Run the docker compose run command
    # Start containers with force recreate
    if "up" in start_command and "--force-recreate" not in start_command:
        start_command += " --force-recreate"
    await _run_docker_compose_command(start_command, target_dir)

    # Wait for the service to become healthy
    if healthcheck_url:
        logger.info("Waiting for Docker service to become healthy", url=healthcheck_url)
        if await wait_for_service_health(healthcheck_url):
            logger.info("Docker service is healthy.")
        else:
            logger.error("Docker service failed to become healthy within timeout.")
            await _run_docker_compose_command("docker compose down", target_dir)
            raise RuntimeError("Docker service failed to become healthy.")
    else:
        logger.warning("No healthcheck URL provided, assuming service started successfully.")


async def restart_docker_service(
    service_data: PluginServiceRuntimeDTO,
    target_dir: Path,
    env_vars: Dict[str, str],
    required_vars: List[str]
):
    """
    Handles the startup/restart of an **already installed** Docker Compose-based service.
    It writes the latest environment variables, starts the Docker containers,
    and checks service health. It explicitly **avoids building images**
    to ensure fast startup times, relying on pre-built images.
    """
    logger.info("Starting Docker service containers", name=service_data.name)

    await check_docker_availability()

    start_command = service_data.start_command
    healthcheck_url = service_data.healthcheck_url
    
    if not start_command:
        raise ValueError("Missing 'start_command' for Docker service.")

    # Write environment file
    write_env_file(target_dir, env_vars, required_vars)

    # Run the Docker Compose start command
    await _run_docker_compose_command(start_command, target_dir)

    # Wait for the service to become healthy
    if healthcheck_url:
        logger.info("Waiting for Docker service to become healthy", url=healthcheck_url)
        if await wait_for_service_health(healthcheck_url):
            logger.info("Docker service is healthy.")
        else:
            logger.error("Docker service failed to become healthy within timeout.")
            await _run_docker_compose_command("docker compose stop", target_dir)
            raise RuntimeError("Docker service failed to become healthy.")
    else:
        logger.warning("No healthcheck URL provided, assuming service started successfully.")


async def stop_docker_service(service_data: PluginServiceRuntimeDTO, target_dir: Path):
    """
    Stops and removes a running docker-compose service.
    """
    logger.info("Attempting to stop docker-compose service", service=service_data.name)
    
    command = "docker compose stop"

    try:
        await _run_docker_compose_command(command, target_dir)
        return True
    except RuntimeError:
        # We don't want to fail the entire shutdown process if one service fails to stop.
        logger.error("Failed to stop docker-compose service gracefully", service=service_data.name)
        return False
