"""HTTP adapter for the permanent conversation; no runtime bootstrap here."""

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from forge_core.conversation_read import (
    ConversationCursorConflict,
    read_committed_turn,
    read_conversation,
)
from forge_core.domain import DomainError
from forge_core.providers import ProviderError


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=20000)
    request_id: str | None = Field(default=None, min_length=1, max_length=200)


def conversation_routes(*, get_store, submit_turn):
    routes = APIRouter(prefix="/api/conversation")

    @routes.get("")
    def get_conversation(
        response: Response,
        before: str | None = Query(default=None, max_length=32),
        limit: int = Query(default=200, ge=1, le=200),
        after: str | None = Query(default=None, max_length=96),
    ):
        try:
            page = read_conversation(get_store(), before=before, after=after, limit=limit)
            response.headers["Cache-Control"] = "no-store"
            return page
        except ConversationCursorConflict:
            raise HTTPException(
                status_code=409, detail="Conversation cursor must be refreshed"
            ) from None
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid conversation cursor") from None

    @routes.get("/turn")
    def get_committed_turn(response: Response, request_id: str = Query(min_length=1, max_length=200)):
        response.headers["Cache-Control"] = "no-store"
        return read_committed_turn(get_store(), request_id)

    @routes.post("/message")
    async def send_message(payload: MessageIn):
        try:
            return await submit_turn(payload.content, request_id=payload.request_id)
        except DomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(
                status_code=503, detail=f"Provider unavailable: {exc.code}"
            ) from exc

    return routes
