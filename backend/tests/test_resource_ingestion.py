from modules.resource_ingestion import collect_youtube_urls, extract_youtube_video_id


def test_extract_youtube_video_id_variants():
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_video_id("https://example.com/watch?v=dQw4w9WgXcQ") is None


def test_collect_youtube_urls_from_mixed_text_and_dedupe():
    refs = [
        "Read this first: https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "And this one: https://www.youtube.com/watch?v=aqz-KE-bpKQ",
    ]
    urls = collect_youtube_urls(refs, max_urls=3)
    assert urls == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
    ]
