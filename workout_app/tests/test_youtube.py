import pytest

from app.services.youtube import parse_video_id


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://evil.example/watch?v=dQw4w9WgXcQ", None),
        ("https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ", None),
        ("javascript:alert(1)", None),
        ("https://www.youtube.com/watch?v=short", None),
        ('https://www.youtube.com/watch?v=dQw4w9WgXcQ"><script>', None),
        ("", None),
    ],
)
def test_parse_video_id(url, expected):
    assert parse_video_id(url) == expected


def test_exercise_with_video_embeds_nocookie(alice):
    resp = alice.post(
        "/exercises/new",
        data={
            "name": "Goblet Squat",
            "sets": 3,
            "reps": "10",
            "rest_seconds": 60,
            "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
        },
    )
    assert resp.status_code == 302
    page = alice.get("/exercises").data
    assert b"https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ" in page


def test_exercise_rejects_non_youtube_url(alice):
    resp = alice.post(
        "/exercises/new",
        data={
            "name": "Squat",
            "sets": 3,
            "reps": "10",
            "rest_seconds": 60,
            "youtube_url": "https://evil.example/video",
        },
    )
    assert resp.status_code == 200
    assert b"doesn&#39;t look like a YouTube video link" in resp.data


def test_exercise_without_video_shows_search_link(alice):
    alice.post("/exercises/new", data={"name": "Face Pull", "sets": 3, "reps": "15", "rest_seconds": 45})
    page = alice.get("/exercises").data
    assert b"youtube.com/results?search_query=how+to+do+Face+Pull" in page
