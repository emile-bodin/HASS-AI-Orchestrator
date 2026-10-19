from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import os
import logging

router = APIRouter(prefix='/api/factory', tags=['factory'])

class GenerateRequest(BaseModel):
    prompt: str

class SaveRequest(BaseModel):
    config: Dict[str, Any]

class UpdateAgentRequest(BaseModel):
    instruction: Optional[str] = None
    name: Optional[str] = None
    entities: Optional[List[str]] = None
    decision_interval: Optional[int] = None


def get_config_path():
    return '/config/agents.yaml' if os.path.exists('/config') else 'agents.yaml'


def lifecycle(request):
    service = getattr(request.app.state, 'agent_lifecycle', None)
    if service is None:
        raise HTTPException(503, 'Agent lifecycle not initialized')
    return service


async def invoke(awaitable):
    try:
        return await awaitable
    except KeyError:
        raise HTTPException(404, 'Agent not found')
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        logging.getLogger(__name__).exception('Agent lifecycle change failed')
        raise HTTPException(500, 'Agent change failed; inspect server logs')


@router.get('/suggestions')
async def suggestions(request: Request):
    architect = getattr(request.app.state, 'architect', None)
    if not architect:
        raise HTTPException(503, 'Architect not initialized')
    return await architect.suggest_agents()


@router.post('/generate')
async def generate(req: GenerateRequest, request: Request):
    architect = getattr(request.app.state, 'architect', None)
    if not architect:
        raise HTTPException(503, 'Architect not initialized')
    return await architect.generate_config(req.prompt)


@router.get('/entities')
async def entities(request: Request):
    return {'entities': await invoke(lifecycle(request).entity_catalog())}


@router.post('/save')
async def save_agent(req: SaveRequest, request: Request):
    return await invoke(lifecycle(request).save(req.config))


@router.patch('/agents/{agent_id}')
async def update_agent(agent_id: str, req: UpdateAgentRequest, request: Request):
    # Editing instruction never silently replaces the user's explicit selection.
    cfg = {k: v for k, v in req.model_dump().items() if v is not None}
    cfg['id'] = agent_id
    return await invoke(lifecycle(request).save(cfg, updating=True))


@router.post('/agents/{agent_id}/pause')
async def pause_agent(agent_id: str, request: Request):
    return await invoke(lifecycle(request).pause(agent_id))


@router.post('/agents/{agent_id}/resume')
async def resume_agent(agent_id: str, request: Request):
    return await invoke(lifecycle(request).resume(agent_id))


@router.delete('/agents/{agent_id}')
async def delete_agent(agent_id: str, request: Request):
    return await invoke(lifecycle(request).delete(agent_id))
