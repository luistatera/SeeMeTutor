"""
Grounding citation extraction from ADK events.

When the Gemini model uses the google_search tool, grounding metadata
can arrive via two paths:

1. **Direct path** — ``event.grounding_metadata`` (when google_search is
   the only tool, i.e., built-in mode).
2. **Agent-tool path** — ``event.actions.state_delta["temp:_adk_grounding_metadata"]``
   (when google_search is wrapped as a GoogleSearchAgentTool via
   ``bypass_multi_tools_limit=True``, which is required when the agent
   has multiple tools).

This module handles both paths and returns citation dicts ready to send
to the browser and the test report.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ADK state key used by GoogleSearchAgentTool to store grounding metadata
_ADK_GROUNDING_STATE_KEY = "temp:_adk_grounding_metadata"


def extract_grounding_citations(event: Any) -> list[dict[str, Any]]:
    """Extract grounding citations from an ADK Event.

    Checks both the direct ``event.grounding_metadata`` field and the
    agent-tool ``event.actions.state_delta`` path.

    Returns a list of citation dicts, each with keys:
        snippet, source, url, query
    """
    # --- Path 1: direct grounding_metadata on the event ---
    metadata = getattr(event, "grounding_metadata", None)

    # --- Path 2: agent-tool stores metadata in state_delta ---
    if metadata is None:
        actions = getattr(event, "actions", None)
        if actions is not None:
            state_delta = getattr(actions, "state_delta", None) or {}
            metadata = state_delta.get(_ADK_GROUNDING_STATE_KEY)

    if metadata is None:
        return []

    return _citations_from_metadata(metadata)


def _citations_from_metadata(metadata: Any) -> list[dict[str, Any]]:
    """Parse a GroundingMetadata object into a list of citation dicts."""
    citations: list[dict[str, Any]] = []

    chunks = getattr(metadata, "grounding_chunks", None) or []
    queries = getattr(metadata, "web_search_queries", None) or []

    # grounding_supports link model output segments to source chunks.
    supports = getattr(metadata, "grounding_supports", None) or []
    support_snippets: dict[int, str] = {}
    for sup in supports:
        seg = getattr(sup, "segment", None)
        seg_text = (getattr(seg, "text", "") or "").strip()
        if not seg_text:
            continue
        for idx in getattr(sup, "grounding_chunk_indices", None) or []:
            if idx not in support_snippets:
                support_snippets[idx] = seg_text

    for chunk_idx, chunk in enumerate(chunks):
        web = getattr(chunk, "web", None)
        if web is None:
            continue
        uri = getattr(web, "uri", "") or ""
        title = getattr(web, "title", "") or ""
        if not uri and not title:
            continue

        # Extract domain for compact display
        domain = ""
        if uri:
            try:
                domain = urlparse(uri).netloc.replace("www.", "")
            except Exception:
                domain = uri[:60]

        # Prefer grounded text segment; fall back to page title
        snippet = support_snippets.get(chunk_idx, "")
        if not snippet:
            snippet = title[:200] if title else ""

        citations.append(
            {
                "snippet": snippet[:300],
                "source": domain or title[:40],
                "url": uri,
                "query": queries[0] if queries else "",
            }
        )

    if citations:
        logger.info(
            "GROUNDING: extracted %d citations, queries=%s",
            len(citations),
            queries[:3],
        )

    return citations
