"""
Search grounding module — extract and forward grounding citations.

Parses grounding metadata from Gemini/ADK responses and produces
citation dicts that can be sent to the browser as {type: "grounding"} events.
"""

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State initialization
# ---------------------------------------------------------------------------
def init_grounding_state() -> dict:
    """Return initial grounding state keys to merge into runtime_state."""
    return {
        "grounding_events": 0,
        "grounding_citations_sent": 0,
        "grounding_search_queries": [],
    }


# ---------------------------------------------------------------------------
# Grounding metadata extraction
# ---------------------------------------------------------------------------
def extract_grounding(event) -> list[dict[str, Any]]:
    """Extract grounding citations from an ADK event or raw Gemini message.

    Checks multiple locations where grounding metadata may appear:
    - event.grounding_metadata  (ADK event)
    - event.server_content.grounding_metadata  (raw Gemini msg)
    """
    citations: list[dict[str, Any]] = []

    # Collect all candidate grounding metadata objects
    candidates = [
        getattr(event, "grounding_metadata", None),
        getattr(
            getattr(event, "server_content", None),
            "grounding_metadata",
            None,
        ),
    ]

    for obj in candidates:
        if obj is None:
            continue

        chunks = getattr(obj, "grounding_chunks", None) or []
        queries = getattr(obj, "web_search_queries", None) or []

        # grounding_supports contain actual grounded text segments
        supports = getattr(obj, "grounding_supports", None) or []
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

            domain = ""
            if uri:
                try:
                    domain = urlparse(uri).netloc.replace("www.", "")
                except Exception:
                    domain = uri[:60]

            snippet = support_snippets.get(chunk_idx, "")
            if not snippet:
                snippet = title[:200] if title else ""

            citations.append({
                "snippet": snippet[:300],
                "source": domain or title[:40],
                "url": uri,
                "query": queries[0] if queries else "",
            })

        # Only process the first valid metadata object
        if citations:
            break

    return citations
