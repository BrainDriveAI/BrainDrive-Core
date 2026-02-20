import asyncio
import importlib.util
import inspect
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
        require_user_bootstrap: bool = bool(payload.get("require_user_bootstrap", False))

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
        self._patch_schema_loader_compat(service_ops)

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
                prepare_kwargs = {
                    "full_install": full_install,
                    "force_recreate": force_recreate,
                }
                installer_user_id = payload.get("installer_user_id") or context.user_id
                if installer_user_id and self._accepts_kwarg(
                    service_ops.prepare_service,
                    "installer_user_id",
                ):
                    prepare_kwargs["installer_user_id"] = installer_user_id

                raw_install_result = await _maybe_await(
                    service_ops.prepare_service,
                    key,
                    **prepare_kwargs,
                )
                install_result = (
                    raw_install_result
                    if isinstance(raw_install_result, dict)
                    else {"success": bool(raw_install_result), "result": raw_install_result}
                )

                bootstrap_result = (
                    install_result.get("bootstrap_user")
                    if isinstance(install_result, dict)
                    else None
                )
                bootstrap_fn = getattr(service_ops, "bootstrap_installer_user", None)
                needs_bootstrap = not (
                    isinstance(bootstrap_result, dict)
                    and bool(bootstrap_result.get("success"))
                )
                if installer_user_id and callable(bootstrap_fn) and needs_bootstrap:
                    raw_bootstrap_result = await _maybe_await(
                        bootstrap_fn,
                        key,
                        installer_user_id,
                    )
                    bootstrap_result = (
                        raw_bootstrap_result
                        if isinstance(raw_bootstrap_result, dict)
                        else {
                            "success": bool(raw_bootstrap_result),
                            "result": raw_bootstrap_result,
                        }
                    )

                if bootstrap_result is not None:
                    install_result["bootstrap_user"] = bootstrap_result

                if require_user_bootstrap:
                    if not installer_user_id:
                        install_result["success"] = False
                        install_result["error"] = (
                            "installer_user_id missing for required user bootstrap"
                        )
                    elif not (
                        isinstance(bootstrap_result, dict)
                        and bool(bootstrap_result.get("success"))
                    ):
                        install_result["success"] = False
                        if not callable(bootstrap_fn):
                            install_result["error"] = (
                                "service_ops module missing bootstrap_installer_user"
                            )
                        else:
                            install_result["error"] = "installer user bootstrap failed"

                start_result = {
                    "success": False,
                    "skipped": True,
                    "reason": "auto_start_disabled",
                }
                health = {"skipped": True, "reason": "auto_start_disabled"}
                if _should_auto_start(key):
                    if not bool(install_result.get("success", True)):
                        start_result = {
                            "success": False,
                            "skipped": True,
                            "reason": "install_failed",
                        }
                        health = {"skipped": True, "reason": "install_failed"}
                    else:
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

                results.append(
                    {
                        "service": key,
                        **install_result,
                        "start": start_result,
                        "health": health,
                    }
                )
                await context.report_progress(
                    stage="service_completed",
                    message=f"{key} install/start done",
                    percent=int(idx * 100 / total),
                    data={
                        "service": key,
                        "install": {
                            k: install_result.get(k)
                            for k in ["success", "code", "stderr", "error"]
                            if k in install_result
                        },
                        "bootstrap_user": (
                            bootstrap_result
                            if isinstance(bootstrap_result, dict)
                            else None
                        ),
                        "start": {
                            k: start_result.get(k)
                            for k in ["success", "code", "stderr"]
                            if k in start_result
                        },
                        "health": health,
                    },
                )
            except Exception as exc:
                logger.exception("Service install failed for %s", key)
                results.append({"service": key, "success": False, "error": str(exc)})

        await context.report_progress(
            stage="completed",
            message="Service install completed",
            percent=100,
            data={"services": results},
        )
        return {"services": results}

    @staticmethod
    def _accepts_kwarg(func: Any, kwarg: str) -> bool:
        try:
            signature = inspect.signature(func)
        except Exception:
            return False

        if kwarg in signature.parameters:
            return True

        return any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    @staticmethod
    def _patch_schema_loader_compat(service_ops: Any) -> None:
        apply_schema_fn = getattr(service_ops, "_apply_schema", None)
        if not callable(apply_schema_fn):
            return

        if getattr(service_ops, "_bd_safe_schema_loader", False):
            return

        def _safe_apply_schema(service: Any, scoped_root: Path) -> List[str]:
            import importlib.util as _importlib_util
            import sys as _sys
            from pathlib import Path as _Path

            schema_module_path = service.repo_path / "app" / "library_schema.py"
            if not schema_module_path.exists():
                return []

            spec = _importlib_util.spec_from_file_location(
                f"library_service_schema_{abs(hash(str(schema_module_path)))}",
                schema_module_path,
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Unable to import schema module: {schema_module_path}")

            module_name = spec.name or f"library_service_schema_{abs(hash(str(schema_module_path)))}"
            module = _importlib_util.module_from_spec(spec)
            _sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            ensure_fn = getattr(module, "ensure_scoped_library_structure", None)
            if not callable(ensure_fn):
                return []

            result = ensure_fn(scoped_root, include_digest_period_files=True)
            changed_paths = getattr(result, "changed_paths", None)
            if not changed_paths:
                return []

            normalized: List[str] = []
            for item in changed_paths:
                try:
                    normalized.append(_Path(item).as_posix())
                except Exception:
                    normalized.append(str(item))
            return normalized

        setattr(service_ops, "_apply_schema", _safe_apply_schema)
        setattr(service_ops, "_bd_safe_schema_loader", True)

    def _load_ops_module(self, path: Path):
        spec = importlib.util.spec_from_file_location("service_install.ops_runtime", path)
        if not spec or not spec.loader:
            raise RuntimeError(f"Unable to load service_ops from {path}")

        module_name = spec.name or "service_install.ops_runtime"
        module = importlib.util.module_from_spec(spec)

        import sys

        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore

        if not hasattr(module, "prepare_service"):
            raise RuntimeError("service_ops module missing prepare_service")
        return module
