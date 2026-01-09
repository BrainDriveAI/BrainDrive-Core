import structlog
from typing import List, Optional

from app.core.job_manager_provider import get_job_manager
from app.core.database import get_db
from app.plugins.repository import PluginRepository
from app.plugins.service_installler.plugin_service_manager import (
    start_plugin_services,
    stop_plugin_services,
    restart_plugin_services,
    resolve_service_ops_path,
    venv_ready,
)

logger = structlog.get_logger()

async def _enqueue_service_install_job(
    repo: PluginRepository,
    *,
    plugin_slug: str,
    user_id: str,
    service_keys: List[str],
) -> Optional[str]:
    if not service_keys:
        return None
    plugin_data = await repo.get_plugin_by_slug(plugin_slug, user_id)
    if not plugin_data:
        logger.warning("Unable to find plugin for service install enqueue", plugin_slug=plugin_slug, user_id=user_id)
        return None
    service_ops_path = resolve_service_ops_path(plugin_slug, plugin_data.get("version"))
    if not service_ops_path:
        logger.warning("Service ops not found; skipping install enqueue", plugin_slug=plugin_slug, user_id=user_id)
        return None
    try:
        job_manager = await get_job_manager()
    except Exception as exc:
        logger.warning("Job manager unavailable; skipping install enqueue", plugin_slug=plugin_slug, error=str(exc))
        return None
    payload = {
        "service_ops_path": str(service_ops_path),
        "service_keys": service_keys,
        "full_install": False,
        "force_recreate": False,
        "auto_start": True,
    }
    job, _ = await job_manager.enqueue_job(
        job_type="service.install",
        payload=payload,
        user_id=user_id,
        max_retries=1,
    )
    logger.info("Queued service install job", plugin_slug=plugin_slug, job_id=job.id, services=service_keys)
    return job.id


async def start_plugin_services_from_settings_on_startup():
    """This is invoked automatically during backend startup/restart to ensure plugin
    services are restarted with environment variables sourced from DB settings"""
    try:
        logger.info("Starting plugin service runtimes...")
        
        async for db in get_db():
            repo = PluginRepository(db)
            service_runtimes = await repo.get_all_service_runtimes()

            if not service_runtimes:
                logger.info("No plugin services found in the database to start.")
                return
            
            logger.info(f"Found {len(service_runtimes)} service runtimes to start")

            groups = {}
            for runtime in service_runtimes:
                key = (runtime.plugin_slug, runtime.definition_id, runtime.user_id)
                groups.setdefault(key, []).append(runtime)

            for (plugin_slug, definition_id, user_id), runtimes in groups.items():
                try:
                    missing_services = [
                        runtime.name
                        for runtime in runtimes
                        if (runtime.type or "docker-compose") == "venv_process" and not venv_ready(runtime, plugin_slug)
                    ]
                    if missing_services:
                        await _enqueue_service_install_job(
                            repo,
                            plugin_slug=plugin_slug,
                            user_id=user_id,
                            service_keys=missing_services,
                        )
                    logger.info(
                        f"Restarting {len(runtimes)} service(s) "
                        f"for plugin '{plugin_slug}' with settings '{definition_id}' "
                        f"(user_id={user_id})"
                    )
                    await restart_plugin_services(
                        plugin_slug,
                        definition_id,
                        user_id=user_id,
                        allow_install=False,
                    )
                except Exception as service_error:
                    logger.error(
                        f"Failed to restart services for plugin '{plugin_slug}' "
                        f"(definition_id={definition_id}, user_id={user_id}): {service_error}"
                    )
                    continue
            
            break
            
    except Exception as e:
        logger.error(f"Error starting plugin services: {e}")

async def start_plugin_services_on_startup():
    """Start all plugin service runtimes on application startup."""
    try:
        logger.info("Starting plugin service runtimes...")
        
        async for db in get_db():
            repo = PluginRepository(db)
            service_runtimes = await repo.get_all_service_runtimes()

            if not service_runtimes:
                logger.info("No plugin services found in the database to start.")
                return
            
            logger.info(f"Found {len(service_runtimes)} service runtimes to start")
            
            groups = {}
            for runtime in service_runtimes:
                key = (runtime.plugin_slug, runtime.user_id)
                groups.setdefault(key, []).append(runtime)

            for (plugin_slug, user_id), runtimes in groups.items():
                missing_services = [
                    runtime.name
                    for runtime in runtimes
                    if (runtime.type or "docker-compose") == "venv_process" and not venv_ready(runtime, plugin_slug)
                ]
                if missing_services:
                    await _enqueue_service_install_job(
                        repo,
                        plugin_slug=plugin_slug,
                        user_id=user_id,
                        service_keys=missing_services,
                    )
                for service_runtime in runtimes:
                    if service_runtime.name in missing_services:
                        continue
                    try:
                        logger.info(f"Starting service {service_runtime.name} for plugin {plugin_slug}")
                        # Wrap in list if start_plugin_services expects a list
                        await start_plugin_services([service_runtime], plugin_slug, allow_install=False)
                    except Exception as service_error:
                        logger.error(f"Failed to start service {service_runtime.name}: {service_error}")
                        continue
            
            break  # Only process the first db connection
            
    except Exception as e:
        logger.error(f"Error starting plugin services: {e}")

async def stop_all_plugin_services_on_shutdown():
    """
    Stops all plugin service runtimes on application shutdown.
    This function handles the logic of finding and stopping services.
    """
    try:
        logger.info("Stopping all plugin services...")
        
        async for db in get_db():
            repo = PluginRepository(db)
            service_runtimes = await repo.get_all_service_runtimes()

            if not service_runtimes:
                logger.info("No plugin services found in the database to stop.")
                return
            
            logger.info(f"Found {len(service_runtimes)} service runtimes to stop.")

            for service_runtime in service_runtimes:
                plugin_slug = service_runtime.plugin_slug
                if not plugin_slug:
                    logger.warning("Skipping service with no plugin_slug", name=service_runtime.get("name"))
                    continue
                
                await stop_plugin_services([service_runtime], plugin_slug)

    except Exception as e:
        logger.error("Error during shutdown of plugin services", error=str(e))
        # Don't reraise, allow the application to continue shutting down gracefully
