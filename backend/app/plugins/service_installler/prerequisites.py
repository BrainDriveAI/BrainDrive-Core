import os
import structlog
from pathlib import Path
from dotenv import dotenv_values

logger = structlog.get_logger()

def load_env_vars(env_path: Path):
    """
    Loads environment variables from a .env file and returns them as a dictionary.
    """
    if not env_path.exists():
        logger.warning(f"Root .env file not found at {env_path}")
        return {}
    
    try:
        # dotenv_values() is a good way to read the file without modifying os.environ
        # for a quick check.
        env_vars = dotenv_values(env_path)
        return env_vars
    except Exception as e:
        logger.error(f"Failed to load .env file from {env_path}: {e}")
        return {}


def check_required_env_vars(service_name: str, required_vars: list, root_env_path: Path):
    """
    Checks if all required environment variables are set in the root .env file.
    
    Args:
        service_name: The name of the service being installed.
        required_vars: A list of environment variable names.
        root_env_path: The path to the main .env file.
    
    Raises:
        RuntimeError: If any required variable is missing.
    """
    # Load the variables from the root .env file
    env_vars = load_env_vars(root_env_path)
    
    missing_vars = [var for var in required_vars if var not in env_vars or not env_vars[var]]

    if missing_vars:
        logger.error(
            "Missing required environment variables in .env file",
            service=service_name,
            missing_vars=missing_vars
        )
        raise RuntimeError(
            f"Missing required environment variables for service '{service_name}': "
            f"{', '.join(missing_vars)}. Please add them to your main BrainDrive backend .env file at {root_env_path}."
        )

    logger.info("All required environment variables are present.")


def write_env_file(target_dir: Path, env_vars: dict, required_vars: list):
    """
    Creates a .env file in the target directory by reading values from a given dictionary.
    
    Args:
        target_dir: The directory where the .env file will be created.
        env_vars: A dictionary of all available environment variables.
        required_vars: A list of the specific variables to write to the new .env file.
    """
    env_path = target_dir / ".env"
    
    try:
        logger.info(f"Creating .env file for service at {env_path}")
        with open(env_path, "w") as f:
            for var_name in required_vars:
                var_value = env_vars.get(var_name, "")
                f.write(f"{var_name}={var_value}\n")
        # Restrict file permissions to owner-only (contains secrets)
        os.chmod(env_path, 0o600)
    except Exception as e:
        logger.error(f"Failed to create .env file for service: {e}")
        raise RuntimeError(f"Failed to create .env file for service: {e}")

def convert_to_download_url(source_url: str, branch: str = 'main') -> str:
    """
    Convert git repository URLs to download URLs for archives
    """
    source_url = source_url.rstrip('.git')
    
    # GitHub
    if 'github.com' in source_url:
        if source_url.startswith('git@'):
            # Convert SSH to HTTPS
            source_url = source_url.replace('git@github.com:', 'https://github.com/')
        return f"{source_url}/archive/refs/heads/{branch}.zip"
    
    # GitLab
    elif 'gitlab.com' in source_url:
        if source_url.startswith('git@'):
            source_url = source_url.replace('git@gitlab.com:', 'https://gitlab.com/')
        return f"{source_url}/-/archive/{branch}/repository.zip"
    
    # Bitbucket
    elif 'bitbucket.org' in source_url:
        if source_url.startswith('git@'):
            source_url = source_url.replace('git@bitbucket.org:', 'https://bitbucket.org/')
        return f"{source_url}/get/{branch}.zip"
    
    # For other cases, assume it's already a direct download URL
    return source_url
