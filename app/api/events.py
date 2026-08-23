"""Real-time SSE event streaming API endpoints.

Broadcasts live backend events (Campaign started, Message prepared, Message sent,
Message failed, Sender expired, Follow-up due) via Server-Sent Events (SSE).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.services.event_bus import event_bus

router = APIRouter(prefix="/api/events", tags=["Events"])


@router.get("/stream")
@router.get("")
async def stream_events():
    """Stream live domain events via Server-Sent Events (SSE)."""

    async def event_generator():
        # Yield an initial connected event
        yield f"event: connected\ndata: {json.dumps({'status': 'connected'})}\n\n"
        
        async for event in event_bus.subscribe():
            data_payload = {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "occurred_at": event.occurred_at.isoformat(),
            }
            yield f"event: {event.event_type}\ndata: {json.dumps(data_payload)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket):
    """Stream live domain events over WebSocket."""
    await websocket.accept()
    await websocket.send_json({"type": "connected", "event_type": "connected", "payload": {"status": "connected"}})
    try:
        async for event in event_bus.subscribe():
            dot_type = event.event_type.lower().replace("_", ".")
            data_payload = {
                "type": dot_type,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "occurred_at": event.occurred_at.isoformat(),
            }
            await websocket.send_json(data_payload)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        # Surface a failing event-stream subscriber instead of silently killing the socket.
        import traceback
        traceback.print_exc()
        print(f"[Events] WebSocket event-stream error: {exc}")


@router.get("/history")
def get_event_history(limit: int = Query(50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """Retrieve recent event history."""
    return event_bus.get_history(limit=limit)
