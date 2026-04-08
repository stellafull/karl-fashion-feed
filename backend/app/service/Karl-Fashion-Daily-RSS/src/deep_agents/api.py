# src/deep_agents/api.py
"""FastAPI app: single POST /research endpoint with SSE streaming."""

import json
import logging
from typing import Any, List, Optional

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from deep_agents.graph import build_research_graph

logger = logging.getLogger(__name__)

app = FastAPI(title="Fashion Deep Research API", version="1.0.0")

_graph = None

NODE_NAMES = {
    "clarify", "planner", "outline_reviser", "section_pipeline",
    "lead_writer", "synthesizer", "trend_triangulator",
    "reviewer", "reviser", "final_check",
}


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_research_graph(checkpointer=MemorySaver())
    return _graph


class ResearchRequest(BaseModel):
    messages: List[Any]
    object_context: Optional[str] = None
    thread_id: str


@app.post("/research")
async def research(request: ResearchRequest):
    """Start a research job. Returns SSE stream of typed events."""

    async def event_stream():
        graph = get_graph()
        config = {"configurable": {"thread_id": request.thread_id}}
        input_state = {
            "messages": request.messages,
            "object_context": request.object_context,
        }

        full_report = ""
        final_result = None

        try:
            async for chunk in graph.astream(input_state, config=config, stream_mode=["updates", "custom"]):
                mode, data = chunk

                if mode == "custom":
                    # Per-section events emitted by writer_node via get_stream_writer()
                    if isinstance(data, dict) and data.get("type") == "section_done":
                        section_id = data.get("section_id")
                        if isinstance(section_id, str) and section_id:
                            yield f"data: {json.dumps({'type': 'section_done', 'section_id': section_id})}\n\n"
                    continue

                # mode == "updates": data is {node_name: state_update}
                for node_name, state_update in data.items():
                    if node_name not in NODE_NAMES:
                        continue
                    if not isinstance(state_update, dict):
                        continue

                    # Progress event for every completed node
                    yield f"data: {json.dumps({'type': 'progress', 'node': node_name, 'status': 'done'})}\n\n"

                    # Clarification event
                    if node_name == "clarify" and state_update.get("need_clarification"):
                        yield f"data: {json.dumps({'type': 'clarification', 'question': state_update.get('clarification_question', '')})}\n\n"

                    # Track the latest full_report — written by synthesizer and reviser
                    if "full_report" in state_update:
                        full_report = state_update["full_report"]

                    # Track final quality gate result
                    if node_name == "final_check":
                        final_result = state_update.get("final_result")

        except Exception as e:
            logger.error(f"Research pipeline error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

        # Emit report event after stream completes
        if full_report or final_result:
            yield f"data: {json.dumps({'type': 'report', 'content': full_report, 'final_result': final_result})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok"}
