"""Server-sent event adapters."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.events import acquire_sse_slot, question_collection_event_stream

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/questions")
async def question_events() -> StreamingResponse:
    """Stream all question changes through one authenticated connection."""
    await acquire_sse_slot()
    return StreamingResponse(
        question_collection_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
