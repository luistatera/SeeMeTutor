"""
Resource ingestion helpers (currently YouTube transcript extraction).
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, urlparse

try:
    from youtube_transcript_api import (
        YouTubeTranscriptApi,
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )
except Exception:  # pragma: no cover - import guarded at runtime
    YouTubeTranscriptApi = None
    NoTranscriptFound = Exception
    TranscriptsDisabled = Exception
    VideoUnavailable = Exception


_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_DEFAULT_LANGS = ["en", "en-US", "pt", "pt-BR", "de", "de-DE"]


def _sanitize_text(value: str, *, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def extract_urls(raw: str) -> list[str]:
    return [match.group(0).strip(".,);]}>\"'") for match in _URL_PATTERN.finditer(str(raw or ""))]


def extract_youtube_video_id(url: str) -> str | None:
    candidate = str(url or "").strip()
    if not candidate:
        return None

    parsed = urlparse(candidate)
    host = parsed.netloc.lower()
    if host not in _YOUTUBE_HOSTS:
        return None

    video_id = ""
    path = parsed.path or ""

    if host == "youtu.be":
        parts = [p for p in path.split("/") if p]
        if parts:
            video_id = parts[0]
    elif path == "/watch":
        params = parse_qs(parsed.query or "")
        values = params.get("v") or []
        if values:
            video_id = values[0]
    else:
        # /embed/<id>, /shorts/<id>, /live/<id>
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
            video_id = parts[1]

    if not video_id:
        return None
    if not _VIDEO_ID_PATTERN.match(video_id):
        return None
    return video_id


def collect_youtube_urls(resource_refs: list[str], *, max_urls: int = 3) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    for item in resource_refs:
        raw = str(item or "").strip()
        if not raw:
            continue
        candidates = [raw]
        if "http://" in raw or "https://" in raw:
            candidates = extract_urls(raw) or [raw]
        for url in candidates:
            video_id = extract_youtube_video_id(url)
            if not video_id:
                continue
            canonical = f"https://www.youtube.com/watch?v={video_id}"
            if canonical in seen:
                continue
            seen.add(canonical)
            found.append(canonical)
            if len(found) >= max_urls:
                return found
    return found


def _fetch_segments(video_id: str, languages: list[str]) -> tuple[list[dict], str]:
    if YouTubeTranscriptApi is None:
        raise RuntimeError("youtube-transcript-api is not installed")

    # API compatibility across library versions:
    # - v0.x: YouTubeTranscriptApi.get_transcript(...)
    # - newer: YouTubeTranscriptApi().fetch(...)
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        return list(segments or []), ""

    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=languages)
    if hasattr(fetched, "to_raw_data"):
        raw = fetched.to_raw_data()
    else:
        raw = list(fetched or [])
    language_code = str(getattr(fetched, "language_code", "") or "")
    return list(raw or []), language_code


def _fetch_transcript_text(
    video_id: str,
    languages: list[str],
    *,
    per_video_char_limit: int,
) -> tuple[str, str]:
    segments, language_code = _fetch_segments(video_id, languages)
    pieces: list[str] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        text = _sanitize_text(str(item.get("text") or ""), max_len=400)
        if text:
            pieces.append(text)
    transcript = _sanitize_text(" ".join(pieces), max_len=per_video_char_limit)
    return transcript, language_code


async def ingest_youtube_transcripts(
    resource_refs: list[str],
    *,
    languages: list[str] | None = None,
    max_videos: int = 3,
    per_video_char_limit: int = 12000,
    total_context_char_limit: int = 24000,
) -> tuple[list[dict], str]:
    youtube_urls = collect_youtube_urls(resource_refs, max_urls=max_videos)
    if not youtube_urls:
        return [], ""

    lang_priority = [str(code).strip() for code in (languages or _DEFAULT_LANGS) if str(code).strip()]
    if not lang_priority:
        lang_priority = list(_DEFAULT_LANGS)

    materials: list[dict] = []
    context_chunks: list[str] = []

    for url in youtube_urls:
        video_id = extract_youtube_video_id(url)
        if not video_id:
            continue
        try:
            transcript, language_code = await asyncio.to_thread(
                _fetch_transcript_text,
                video_id,
                lang_priority,
                per_video_char_limit=per_video_char_limit,
            )
            if not transcript:
                materials.append(
                    {
                        "kind": "youtube",
                        "url": url,
                        "video_id": video_id,
                        "status": "unavailable",
                        "error": "No transcript text available.",
                    }
                )
                continue
            excerpt = _sanitize_text(transcript, max_len=260)
            materials.append(
                {
                    "kind": "youtube",
                    "url": url,
                    "video_id": video_id,
                    "status": "ready",
                    "language": language_code or "",
                    "char_count": len(transcript),
                    "excerpt": excerpt,
                }
            )
            context_chunks.append(f"Source: {url}\nTranscript:\n{transcript}")
        except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as exc:
            materials.append(
                {
                    "kind": "youtube",
                    "url": url,
                    "video_id": video_id,
                    "status": "unavailable",
                    "error": _sanitize_text(str(exc), max_len=160),
                }
            )
        except Exception as exc:  # pragma: no cover - defensive path
            materials.append(
                {
                    "kind": "youtube",
                    "url": url,
                    "video_id": video_id,
                    "status": "error",
                    "error": _sanitize_text(str(exc), max_len=160),
                }
            )

    context_blob = _sanitize_text("\n\n".join(context_chunks), max_len=total_context_char_limit)
    return materials, context_blob
