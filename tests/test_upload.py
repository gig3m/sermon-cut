from pathlib import Path
from sermon_cut.plan import SermonPlan, Chapter
from sermon_cut.upload import (
    build_description, upload_video, VideoMetadata, format_video_title,
    format_shorts_title, build_shorts_description,
    write_upload_receipt, load_upload_receipt,
)


def test_format_video_title_appends_formatted_date():
    plan = SermonPlan(
        start=0.0, end=1.0, title="Remember the Sabbath Day", speaker="Clay Thornton",
        date="2026-07-05", description="d",
    )
    assert format_video_title(plan) == "Remember the Sabbath Day — July 5, 2026"


def test_build_description_offsets_chapters_to_final_video():
    plan = SermonPlan(
        start=3000.0, end=4500.0, title="Faith", speaker="Pastor Dan",
        date="2026-06-28", description="On Hebrews 11.",
        chapters=[Chapter(3000.0, "Intro"), Chapter(3600.0, "Point One")],
    )
    text = build_description(plan, "Footer line.", intro_seconds=5,
                             front_padding_seconds=0, dissolve_seconds=0)
    # YouTube needs a 00:00 chapter to render the chapter list at all
    assert "00:00 Introduction" in text
    # chapter at sermon start -> 5s into final video -> 00:05
    assert "00:05 Intro" in text
    # chapter 600s after sermon start + 5s intro -> 605s -> 10:05
    assert "10:05 Point One" in text
    assert "On Hebrews 11." in text
    assert "Footer line." in text
    assert "Chapters:" in text


def test_build_description_includes_front_padding_in_offsets():
    plan = SermonPlan(
        start=3000.0, end=4500.0, title="Faith", speaker="Pastor Dan",
        date="2026-06-28", description="d",
        chapters=[Chapter(3600.0, "Point One")],
    )
    # 600s after start + 5s intro + 8s front padding = 613s -> 10:13
    text = build_description(plan, "", intro_seconds=5, front_padding_seconds=8,
                             dissolve_seconds=0)
    assert "00:00 Introduction" in text
    assert "10:13 Point One" in text


def test_build_description_subtracts_dissolve_overlap():
    plan = SermonPlan(
        start=3000.0, end=4500.0, title="Faith", speaker="Pastor Dan",
        date="2026-06-28", description="d",
        chapters=[Chapter(3600.0, "Point One")],
    )
    # the intro->sermon dissolve pulls the sermon 0.8s earlier:
    # 600 + 5 (intro) + 0 (front) - 0.8 (dissolve) = 604.2 -> 10:04
    text = build_description(plan, "", intro_seconds=5, front_padding_seconds=0,
                             dissolve_seconds=0.8)
    assert "10:04 Point One" in text


class FakeInsert:
    def __init__(self, store): self.store = store
    def execute(self): return {"id": "newvid42"}


class FakeVideos:
    def __init__(self, store): self.store = store
    def insert(self, **kwargs):
        self.store["insert_kwargs"] = kwargs
        return FakeInsert(self.store)


class FakeThumbSet:
    def __init__(self, store): self.store = store
    def execute(self): return {}


class FakeThumbnails:
    def __init__(self, store): self.store = store
    def set(self, **kwargs):
        self.store["thumb_kwargs"] = kwargs
        return FakeThumbSet(self.store)


class FakeYouTube:
    def __init__(self): self.store = {}
    def videos(self): return FakeVideos(self.store)
    def thumbnails(self): return FakeThumbnails(self.store)


def test_upload_video_inserts_and_returns_url(tmp_path: Path):
    video = tmp_path / "final.mp4"; video.write_bytes(b"v")
    yt = FakeYouTube()
    meta = VideoMetadata(title="Faith", description="desc", tags=["sermon"])
    # media_factory avoids touching real MediaFileUpload
    url = upload_video(video, meta, youtube_client=yt,
                       media_factory=lambda path: f"MEDIA:{path}")
    assert url == "https://youtu.be/newvid42"
    body = yt.store["insert_kwargs"]["body"]
    assert body["snippet"]["title"] == "Faith"
    assert body["status"]["privacyStatus"] == "unlisted"
    assert yt.store["insert_kwargs"]["part"] == "snippet,status"
    # no thumbnail provided -> thumbnails.set not called
    assert "thumb_kwargs" not in yt.store


def test_upload_video_sets_thumbnail_when_provided(tmp_path: Path):
    video = tmp_path / "final.mp4"; video.write_bytes(b"v")
    thumb = tmp_path / "title_card.png"; thumb.write_bytes(b"png")
    yt = FakeYouTube()
    meta = VideoMetadata(title="Faith", description="desc")
    upload_video(video, meta, youtube_client=yt, thumbnail_path=thumb,
                 media_factory=lambda path: f"MEDIA:{path}")
    assert yt.store["thumb_kwargs"]["videoId"] == "newvid42"
    assert yt.store["thumb_kwargs"]["media_body"] == f"MEDIA:{thumb}"


def test_upload_video_skips_missing_thumbnail(tmp_path: Path):
    video = tmp_path / "final.mp4"; video.write_bytes(b"v")
    yt = FakeYouTube()
    meta = VideoMetadata(title="Faith", description="desc")
    upload_video(video, meta, youtube_client=yt,
                 thumbnail_path=tmp_path / "nope.png",
                 media_factory=lambda path: f"MEDIA:{path}")
    assert "thumb_kwargs" not in yt.store


def test_shorts_title_joins_candidate_and_sermon():
    assert format_shorts_title("Where Do You Run?", "Cities of Refuge") == \
        "Where Do You Run? — Cities of Refuge"


def test_upload_receipt_round_trip(tmp_path):
    assert load_upload_receipt(tmp_path) is None
    write_upload_receipt(tmp_path, "vid123", "https://youtu.be/vid123")
    got = load_upload_receipt(tmp_path)
    assert got["video_id"] == "vid123"
    assert got["url"] == "https://youtu.be/vid123"


def test_shorts_description_has_hashtag_and_optional_link():
    d = build_shorts_description("Where Do You Run?", "A hook.", "Footer.",
                                 sermon_url="https://youtu.be/abc")
    assert "#Shorts" in d
    assert "Full sermon: https://youtu.be/abc" in d
    assert "Footer." in d
    d2 = build_shorts_description("t", "r", "", sermon_url=None)
    assert "Full sermon:" not in d2
