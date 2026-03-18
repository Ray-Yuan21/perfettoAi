"""Settings and models configuration routes."""

from fastapi import APIRouter, Request

from ..config import ConfigManager
from ..dependencies import get_services

router = APIRouter(prefix="/api", tags=["settings"])
services = get_services()

@router.get("/settings")
async def get_settings():
    """Return current LLM configuration (key masked)."""
    config = services.state_manager.config or ConfigManager.load()
    llm = config.llm
    return {
        "provider": llm.provider,
        "api_endpoint": llm.api_endpoint,
        "model_name": llm.model_name,
        "api_key_set": bool(llm.api_key),
        "temperature": llm.temperature,
        "max_tokens": llm.max_tokens,
        "timeout": llm.timeout,
    }


@router.post("/settings")
async def update_settings(request: Request):
    """Update LLM configuration at runtime."""
    body = await request.json()
    config = services.state_manager.config or ConfigManager.load()
    llm = config.llm
    if "api_endpoint" in body:
        llm.api_endpoint = body["api_endpoint"]
    if "api_key" in body and body["api_key"]:
        llm.api_key = body["api_key"]
    if "model_name" in body:
        llm.model_name = body["model_name"]
    if "provider" in body:
        llm.provider = body["provider"]
    if "timeout" in body:
        llm.timeout = int(body["timeout"])
    services.state_manager.config = config
    return {"ok": True}


@router.get("/models")
async def list_models():
    """Query available models from the LLM API endpoint."""
    config = services.state_manager.config or ConfigManager.load()
    llm = config.llm
    url = f"{llm.api_endpoint.rstrip('/')}/models"
    headers = {
        "Authorization": f"Bearer {llm.api_key}",
        "x-api-key": llm.api_key,
    }
    try:
        if services.http_client:
            resp = await services.http_client.get(url, headers=headers, timeout=10.0)
            data = resp.json()
            models = [m.get("id", "") for m in data.get("data", [])]
            return {"models": sorted(models), "current": llm.model_name}
        return {"models": [], "current": llm.model_name, "error": "No http_client"}
    except Exception as e:
        return {"models": [], "current": llm.model_name, "error": str(e)}
