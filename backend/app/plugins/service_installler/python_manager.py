import os
import subprocess
from pathlib import Path
import structlog

from .service_health_checker import wait_for_service_health

logger = structlog.get_logger()

async def install_python_service(service: dict, target_dir: Path):
    """
    Handle the installation and startup of a service using a virtual environment.
    """
    name = service["name"]
    install_command = service["install_command"]
    start_command = service["start_command"]
    healthcheck_url = service["healthcheck_url"]
    
    # Create virtual environment
    venv_dir = target_dir / "venv"
    if not venv_dir.exists():
        logger.info(f"Creating virtualenv for {name}")
        result = subprocess.run(["python", "-m", "venv", str(venv_dir)],
                                 capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create venv for {name}: {result.stderr}")
    else:
        logger.info(f"Virtualenv already exists for {name}, skipping.")
    
    # Determine Python executable inside venv
    venv_python = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
    
    # Install dependencies
    logger.info(f"Installing dependencies for {name}")
    import shlex
    install_cmd = [str(venv_python)] + shlex.split(install_command)
    result = subprocess.run(install_cmd, cwd=target_dir,
                            capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"Failed to install dependencies for {name}: {result.stderr}")
        raise RuntimeError(f"Failed to install dependencies for service {name}")
    
    # Start service
    logger.info(f"Starting service {name}")
    start_cmd = [str(venv_python)] + shlex.split(start_command)
    proc = subprocess.Popen(start_cmd, cwd=target_dir,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Save PID
    pid_file = target_dir / "service.pid"
    pid_file.write_text(str(proc.pid))
    
    # Healthcheck with async HTTP
    logger.info(f"Waiting for service {name} to become healthy...")
    if await wait_for_service_health(healthcheck_url, timeout=30):
        logger.info(f"Service {name} is healthy and running.")
    else:
        # Try to get error output from the process
        try:
            stdout, stderr = proc.communicate(timeout=1)
            logger.error(f"Service {name} failed to start. Stdout: {stdout.decode()}, Stderr: {stderr.decode()}")
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeError(f"Service {name} failed to start within 30 seconds")
