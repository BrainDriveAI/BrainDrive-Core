from fastapi import APIRouter, HTTPException, Depends, Body, Response
from fastapi.responses import StreamingResponse
import httpx
import json
import asyncio
from typing import Optional, List, Dict, Any, AsyncGenerator
from pydantic import BaseModel, AnyHttpUrl
from urllib.parse import unquote
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.job_manager_provider import get_job_manager
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.models.job import Job, JobStatus
from app.models.settings import SettingDefinition, SettingScope
from app.models.user import User
from app.services.job_manager import HandlerRegistrationError, JobManager
from app.utils.ollama import normalize_server_base, make_dedupe_key

router = APIRouter()

class OllamaResponse(BaseModel):
    status: str
    version: Optional[str] = None

class ModelInstallRequest(BaseModel):
    name: str
    server_url: str
    api_key: Optional[str] = None
    stream: Optional[bool] = True
    force_reinstall: bool = False

class ModelDeleteRequest(BaseModel):
    name: str
    server_url: str
    api_key: Optional[str] = None

INSTALL_JOB_TYPE = "ollama.install"
TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELED.value,
}


def _map_job_status_to_legacy(job: Job) -> str:
    if job.status == JobStatus.QUEUED.value:
        return "queued"
    if job.status == JobStatus.RUNNING.value:
        stage = (job.current_stage or "").lower()
        if stage in {"downloading", "verifying", "extracting", "finalizing"}:
            return stage
        return "running"
    if job.status == JobStatus.COMPLETED.value:
        return "completed"
    if job.status == JobStatus.FAILED.value:
        return "error"
    if job.status == JobStatus.CANCELED.value:
        return "canceled"
    return "queued"


def _serialize_install_job(job: Job) -> Dict[str, Any]:
    payload = job.payload or {}
    return {
        "task_id": job.id,
        "name": payload.get("model_name"),
        "server_base": payload.get("server_url"),
        "state": _map_job_status_to_legacy(job),
        "progress": job.progress_percent,
        "message": job.message or "",
        "error": job.error_message,
        "created_at": job.created_at.timestamp() if job.created_at else None,
        "updated_at": job.updated_at.timestamp() if job.updated_at else None,
    }


def _serialize_install_event(job: Job, event) -> Dict[str, Any]:
    payload = _serialize_install_job(job)
    data = getattr(event, "data", {}) or {}
    if "stage" in data:
        payload["state"] = data["stage"]
    if "message" in data:
        payload["message"] = data["message"]
    if "progress_percent" in data:
        payload["progress"] = data["progress_percent"]
    payload.update({k: v for k, v in data.items() if k not in {"model_name", "server_url"}})
    payload["event_type"] = event.event_type
    payload["sequence_number"] = event.sequence_number
    payload["timestamp"] = event.timestamp.isoformat()
    return payload


def _ensure_install_job(job: Optional[Job], auth: AuthContext) -> Job:
    if not job or job.job_type != INSTALL_JOB_TYPE:
        raise HTTPException(status_code=404, detail="Task not found")
    if job.user_id != str(auth.user_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return job
async def ensure_ollama_settings_definition(db: AsyncSession):
    """Ensure the Ollama settings definition exists"""
    definition = await SettingDefinition.get_by_id(db, 'ollama_settings')
    if not definition:
        definition = SettingDefinition(
            id='ollama_settings',
            name='Ollama Server Settings',
            description='Configuration for the Ollama server connection',
            category='servers',
            type='object',
            allowed_scopes=[SettingScope.USER],
            validation={
                'required': ['serverAddress', 'serverName'],
                'properties': {
                    'serverAddress': {'type': 'string', 'format': 'uri'},
                    'serverName': {'type': 'string', 'minLength': 1},
                    'apiKey': {'type': 'string'}
                }
            }
        )
        await definition.save(db)
    return definition

@router.get("/test", response_model=OllamaResponse)
async def test_ollama_connection(
    server_url: str,
    api_key: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Test connection to an Ollama server by checking its version endpoint
    """
    # Ensure settings definition exists
    # await ensure_ollama_settings_definition(db)

    try:
        # Clean and validate the URL
        server_url = unquote(server_url).strip()
        if not server_url.startswith(('http://', 'https://')):
            raise HTTPException(
                status_code=400,
                detail="Invalid server URL. Must start with http:// or https://"
            )

        # Ensure the URL ends with /api/version
        if not server_url.endswith('/api/version'):
            server_url = server_url.rstrip('/') + '/api/version'
        
        # Prepare headers
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        # Set a reasonable timeout and disable redirects
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            try:
                response = await client.get(server_url, headers=headers)
                if response.status_code == 200:
                    return OllamaResponse(
                        status="success",
                        version=response.json().get("version", "unknown")
                    )
                else:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Server returned status code {response.status_code}"
                    )
            except httpx.TimeoutException:
                raise HTTPException(
                    status_code=504,
                    detail="Connection timed out. Server might be down or unreachable."
                )
            except httpx.ConnectError:
                raise HTTPException(
                    status_code=503,
                    detail="Could not connect to server. Please check if the server is running."
                )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"Error connecting to Ollama server: {str(e)}"
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

async def stream_response(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    """
    Stream the response from Ollama API
    
    Ollama's streaming format is a series of newline-delimited JSON objects.
    Each JSON object contains a 'response' field with a chunk of text.
    For model installation, it contains progress information.
    """
    try:
        # Check if response is successful
        if response.status_code != 200:
            error_json = json.dumps({"error": f"Server returned status {response.status_code}"})
            yield error_json.encode() + b'\n'
            return

        # Try to read the response content
        try:
            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        # Parse the JSON and yield it back as a properly formatted JSON line
                        data = json.loads(line)
                        # Add a newline to ensure proper streaming format
                        yield json.dumps(data).encode() + b'\n'
                    except json.JSONDecodeError:
                        # If it's not valid JSON, just pass it through
                        yield line.encode() + b'\n'
        except Exception as stream_error:
            # If streaming fails, try to get the response content directly
            try:
                content = response.text
                if content.strip():
                    # Try to parse as JSON first
                    try:
                        data = json.loads(content)
                        yield json.dumps(data).encode() + b'\n'
                    except json.JSONDecodeError:
                        # If not JSON, send as plain text
                        yield content.encode() + b'\n'
                else:
                    # Empty response but successful status - send success message
                    success_json = json.dumps({"status": "success", "message": "Operation completed successfully"})
                    yield success_json.encode() + b'\n'
            except Exception as content_error:
                print(f"Error reading response content: {content_error}")
                # Send a generic success message if we can't read the content
                success_json = json.dumps({"status": "success", "message": "Operation completed"})
                yield success_json.encode() + b'\n'
                
    except Exception as e:
        print(f"Error in stream_response: {e}")
        error_json = json.dumps({"error": str(e)})
        yield error_json.encode() + b'\n'

@router.post("/passthrough")
async def ollama_passthrough(
    request_data: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Generic passthrough for Ollama API requests (non-streaming)
    """
    try:
        # Extract required parameters
        server_url = request_data.get("server_url")
        endpoint = request_data.get("endpoint")
        method = request_data.get("method", "GET").upper()
        api_key = request_data.get("api_key")
        payload = request_data.get("payload", {})
        
        # Validate required parameters
        if not server_url or not endpoint:
            raise HTTPException(
                status_code=400,
                detail="server_url and endpoint are required"
            )
            
        # Clean and validate the URL
        server_url = unquote(server_url).strip()
        if not server_url.startswith(('http://', 'https://')):
            raise HTTPException(
                status_code=400,
                detail="Invalid server URL. Must start with http:// or https://"
            )
            
        # Construct the full URL
        full_url = f"{server_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        # Prepare headers
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
            
        # Set a longer timeout for large models and disable redirects
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
            try:
                if method == "GET":
                    response = await client.get(full_url, headers=headers, params=payload)
                elif method == "POST":
                    response = await client.post(full_url, headers=headers, json=payload)
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsupported method: {method}"
                    )
                
                # Return the response data
                return {
                    "status_code": response.status_code,
                    "data": response.json() if response.headers.get("content-type") == "application/json" else response.text
                }
            except httpx.TimeoutException:
                raise HTTPException(
                    status_code=504,
                    detail="Connection timed out. Server might be down or unreachable."
                )
            except httpx.ConnectError:
                raise HTTPException(
                    status_code=503,
                    detail="Could not connect to server. Please check if the server is running."
                )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"Error connecting to Ollama server: {str(e)}"
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@router.post("/stream")
async def ollama_stream(
    request_data: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Streaming endpoint for Ollama API requests
    """
    try:
        # Extract required parameters
        server_url = request_data.get("server_url")
        endpoint = request_data.get("endpoint")
        method = request_data.get("method", "POST").upper()  # Default to POST for streaming
        api_key = request_data.get("api_key")
        payload = request_data.get("payload", {})
        
        # Ensure streaming is enabled in the payload
        if isinstance(payload, dict):
            payload["stream"] = True
        
        # Validate required parameters
        if not server_url or not endpoint:
            raise HTTPException(
                status_code=400,
                detail="server_url and endpoint are required"
            )
            
        # Clean and validate the URL
        server_url = unquote(server_url).strip()
        if not server_url.startswith(('http://', 'https://')):
            raise HTTPException(
                status_code=400,
                detail="Invalid server URL. Must start with http:// or https://"
            )
            
        # Construct the full URL
        full_url = f"{server_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        # Prepare headers
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
            
        # Set a longer timeout for large models and disable redirects
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
            try:
                if method == "GET":
                    response = await client.get(full_url, headers=headers, params=payload, stream=True)
                elif method == "POST":
                    response = await client.post(full_url, headers=headers, json=payload, stream=True)
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsupported method: {method}"
                    )
                
                # Return a streaming response
                return StreamingResponse(
                    stream_response(response),
                    media_type="application/json",
                    status_code=response.status_code
                )
            except httpx.TimeoutException:
                raise HTTPException(
                    status_code=504,
                    detail="Connection timed out. Server might be down or unreachable."
                )
            except httpx.ConnectError:
                raise HTTPException(
                    status_code=503,
                    detail="Could not connect to server. Please check if the server is running."
                )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"Error connecting to Ollama server: {str(e)}"
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@router.get("/models", response_model=List[Dict[str, Any]])
async def get_ollama_models(
    server_url: str,
    api_key: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a list of available models from an Ollama server
    """
    try:
        # Clean and validate the URL
        server_url = unquote(server_url).strip()
        if not server_url.startswith(('http://', 'https://')):
            raise HTTPException(
                status_code=400,
                detail="Invalid server URL. Must start with http:// or https://"
            )

        # Ensure the URL ends with /api/tags
        if not server_url.endswith('/api/tags'):
            server_url = server_url.rstrip('/') + '/api/tags'
        
        # Prepare headers
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        # Set a reasonable timeout and disable redirects
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            try:
                response = await client.get(server_url, headers=headers)
                if response.status_code == 200:
                    # Extract the models from the response
                    models = response.json().get("models", [])
                    return models
                else:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Server returned status code {response.status_code}"
                    )
            except httpx.TimeoutException:
                raise HTTPException(
                    status_code=504,
                    detail="Connection timed out. Server might be down or unreachable."
                )
            except httpx.ConnectError:
                raise HTTPException(
                    status_code=503,
                    detail="Could not connect to server. Please check if the server is running."
                )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"Error connecting to Ollama server: {str(e)}"
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

async def check_model_exists(client: httpx.AsyncClient, server_base: str, model_name: str, headers: dict) -> bool:
    """
    Check if a model already exists on the Ollama server
    """
    try:
        # Construct the models endpoint URL
        base = server_base.rstrip('/')
        models_url = f"{base}/api/tags"
        
        response = await client.get(models_url, headers=headers)
        if response.status_code == 200:
            models_data = response.json()
            models = models_data.get("models", [])
            
            # Check if the model name exists in the list
            for model in models:
                if model.get("name") == model_name:
                    return True
        return False
    except Exception as e:
        print(f"Error checking if model exists: {e}")
        # If we can't check, assume it doesn't exist to allow installation
        return False

@router.post("/install")
async def install_ollama_model(
    request: ModelInstallRequest,
    auth: AuthContext = Depends(require_user),
    job_manager: JobManager = Depends(get_job_manager),
):
    """Enqueue a model installation using the background job system."""
    server_base = normalize_server_base(request.server_url)
    if not server_base.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid server URL. Must start with http:// or https://")

    payload = {
        "model_name": request.name,
        "server_url": server_base,
        "api_key": request.api_key,
        "force_reinstall": request.force_reinstall,
    }

    idempotency_key = make_dedupe_key(server_base, request.name)
    try:
        job, created = await job_manager.enqueue_job(
            job_type=INSTALL_JOB_TYPE,
            payload=payload,
            user_id=str(auth.user_id),
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HandlerRegistrationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"task_id": job.id, "deduped": not created}


@router.get("/install/{task_id}")
async def get_install_status(
    task_id: str,
    auth: AuthContext = Depends(require_user),
    job_manager: JobManager = Depends(get_job_manager),
):
    job = await job_manager.get_job(task_id)
    job = _ensure_install_job(job, auth)
    return _serialize_install_job(job)


@router.get("/install/{task_id}/events")
async def stream_install_events(
    task_id: str,
    auth: AuthContext = Depends(require_user),
    job_manager: JobManager = Depends(get_job_manager),
):
    job = await job_manager.get_job(task_id)
    job = _ensure_install_job(job, auth)

    async def event_generator() -> AsyncGenerator[bytes, None]:
        last_sequence: Optional[int] = None
        yield f"data: {json.dumps(_serialize_install_job(job))}\n\n".encode()

        while True:
            events = await job_manager.get_progress_events(task_id, since=last_sequence)
            if events:
                job_snapshot = await job_manager.get_job(task_id)
                if not job_snapshot or job_snapshot.user_id != str(auth.user_id) or job_snapshot.job_type != INSTALL_JOB_TYPE:
                    break
                for event in events:
                    last_sequence = event.sequence_number
                    payload = _serialize_install_event(job_snapshot, event)
                    yield f"data: {json.dumps(payload)}\n\n".encode()

            job_snapshot = await job_manager.get_job(task_id)
            if not job_snapshot or job_snapshot.user_id != str(auth.user_id) or job_snapshot.job_type != INSTALL_JOB_TYPE:
                break
            if job_snapshot.status in TERMINAL_JOB_STATUSES:
                yield f"data: {json.dumps(_serialize_install_job(job_snapshot))}\n\n".encode()
                break

            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.delete("/install/{task_id}")
async def cancel_install(
    task_id: str,
    auth: AuthContext = Depends(require_user),
    job_manager: JobManager = Depends(get_job_manager),
):
    job = await job_manager.get_job(task_id)
    job = _ensure_install_job(job, auth)
    canceled = await job_manager.cancel_job(task_id)
    if not canceled:
        latest = await job_manager.get_job(task_id)
        if latest and latest.status in TERMINAL_JOB_STATUSES:
            return {"task_id": latest.id, "state": _map_job_status_to_legacy(latest)}
        raise HTTPException(status_code=400, detail="Unable to cancel task")
    return {"task_id": job.id, "state": "canceling"}

@router.delete("/delete")
async def delete_ollama_model(
    request: ModelDeleteRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a model from the Ollama server
    """
    try:
        # Clean and validate the URL
        server_url = unquote(request.server_url).strip()
        if not server_url.startswith(('http://', 'https://')):
            raise HTTPException(
                status_code=400,
                detail="Invalid server URL. Must start with http:// or https://"
            )

        # Ensure the URL ends with /api/delete
        if not server_url.endswith('/api/delete'):
            server_url = server_url.rstrip('/') + '/api/delete'
        
        # Prepare headers
        headers = {'Content-Type': 'application/json'}
        if request.api_key:
            headers['Authorization'] = f'Bearer {request.api_key}'

        # Prepare payload
        payload = {
            "name": request.name
        }

        # Set a reasonable timeout and disable redirects
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            try:
                # Use client.request for DELETE with JSON body
                response = await client.request("DELETE", server_url, headers=headers, json=payload)
                if response.status_code == 200:
                    return {
                        "status": "success",
                        "data": response.json() if response.headers.get("content-type") == "application/json" else response.text
                    }
                else:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Server returned status code {response.status_code}"
                    )
            except httpx.TimeoutException:
                raise HTTPException(
                    status_code=504,
                    detail="Connection timed out. Server might be down or unreachable."
                )
            except httpx.ConnectError:
                raise HTTPException(
                    status_code=503,
                    detail="Could not connect to server. Please check if the server is running."
                )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"Error connecting to Ollama server: {str(e)}"
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )
