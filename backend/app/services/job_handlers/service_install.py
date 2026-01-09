import asyncio
import importlib.util
import logging
from pathlib import Path
from typing import Any, Dict, List

from app.services.job_manager import BaseJobHandler, JobExecutionContext

logger = logging.getLogger(__name__)


class ServiceInstallHandler(BaseJobHandler):
    """Generic service installer using a provided service_ops module."""

    job_type = "service.install"
    display_name = "Service Install"
    description = "Install/start one or more services via a provided service_ops module."
    default_config = {"timeout_seconds": 1800}

    async def validate_payload(self, payload: Dict[str, Any]) -> None:
        if "service_ops_path" not in payload:
            raise ValueError("service_ops_path is required")
        ops_path = Path(payload["service_ops_path"])
        if not ops_path.exists():
            raise ValueError(f"service_ops_path not found: {ops_path}")
        if "service_keys" not in payload or not isinstance(payload["service_keys"], list):
            raise ValueError("service_keys must be a list of service ids")

    async def execute(self, context: JobExecutionContext) -> Dict[str, Any]:
        payload = context.payload
        ops_path = Path(payload["service_ops_path"]).resolve()
        service_keys: List[str] = payload.get("service_keys", [])
        full_install: bool = bool(payload.get("full_install", False))
        force_recreate: bool = bool(payload.get("force_recreate", False))
        auto_start = payload.get("auto_start", True)

        def _should_auto_start(key: str) -> bool:
            if isinstance(auto_start, dict):
                return bool(auto_start.get(key, False))
            return bool(auto_start)

        async def _maybe_await(func, *args, **kwargs):
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result

        await context.report_progress(
            percent=0,
            stage="starting",
            message="Beginning service install",
            data={"services": service_keys},
        )

        service_ops = self._load_ops_module(ops_path)

        results: List[Dict[str, Any]] = []
        total = max(len(service_keys), 1)
        for idx, key in enumerate(service_keys, start=1):
            await context.check_for_cancel()
            await context.report_progress(
                stage="installing",
                message=f"Installing {key}",
                percent=int((idx - 1) * 100 / total),
                data={"service": key},
            )
            try:
                install_result = await service_ops.prepare_service(key, full_install=full_install, force_recreate=force_recreate)
                start_result = {"success": False, "skipped": True, "reason": "auto_start_disabled"}
                health = {"skipped": True, "reason": "auto_start_disabled"}
                if _should_auto_start(key):
                    precheck = None
                    if hasattr(service_ops, "pre_start_check"):
                        precheck = await _maybe_await(service_ops.pre_start_check, key)
                    precheck_failed = False
                    if isinstance(precheck, dict):
                        precheck_failed = not precheck.get("success", True)
                    elif precheck is False:
                        precheck_failed = True
                    if precheck_failed:
                        start_result = {
                            "success": False,
                            "skipped": True,
                            "reason": "pre_start_check_failed",
                            "check": precheck,
                        }
                        health = {"skipped": True, "reason": "pre_start_check_failed"}
                    else:
                        start_result = await _maybe_await(service_ops.start_service, key)
                        health = await _maybe_await(service_ops.health_check, key)
                results.append({"service": key, **install_result, "start": start_result, "health": health})
                await context.report_progress(
                    stage="service_completed",
                    message=f"{key} install/start done",
                    percent=int(idx * 100 / total),
                    data={
                        "service": key,
                        "install": {k: install_result.get(k) for k in ["success", "code", "stderr"] if k in install_result},
                        "start": {k: start_result.get(k) for k in ["success", "code", "stderr"] if k in start_result},
                        "health": health,
                    },
                )
            except Exception as exc:
                logger.exception("Service install failed", service=key)
                results.append({"service": key, "success": False, "error": str(exc)})

        await context.report_progress(
            stage="completed",
            message="Service install completed",
            percent=100,
            data={"services": results},
        )
        return {"services": results}

    def _load_ops_module(self, path: Path):
        spec = importlib.util.spec_from_file_location("service_install.ops_runtime", path)
        if not spec or not spec.loader:
            raise RuntimeError(f"Unable to load service_ops from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        if not hasattr(module, "prepare_service"):
            raise RuntimeError("service_ops module missing prepare_service")
        return module
