from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from .plan import SermonPlan
from .title_card import format_date


def format_video_title(plan: SermonPlan) -> str:
    """The uploaded YouTube title: clean sermon title plus the formatted date."""
    return f"{plan.title} — {format_date(plan.date)}"


def _mmss(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def build_description(plan: SermonPlan, footer: str, *, intro_seconds: int = 0,
                     front_padding_seconds: int = 0,
                     dissolve_seconds: float = 0.0) -> str:
    parts = [plan.description.strip()]
    if plan.chapters:
        # In the final video the sermon is preceded by the intro card, but the
        # intro->sermon cross-dissolve overlaps them by dissolve_seconds, so the
        # sermon starts at (intro_seconds - dissolve_seconds). The clip also
        # carries front_padding_seconds of lead-in before plan.start. YouTube
        # only renders chapters when the first timestamp is exactly 00:00, so we
        # anchor an Introduction chapter there.
        head = intro_seconds - dissolve_seconds + front_padding_seconds
        lines = ["Chapters:", "00:00 Introduction"]
        for ch in plan.chapters:
            offset = (ch.time - plan.start) + head
            lines.append(f"{_mmss(offset)} {ch.label}")
        parts.append("\n".join(lines))
    if footer.strip():
        parts.append(footer.strip())
    return "\n\n".join(parts)


@dataclass
class VideoMetadata:
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    privacy: str = "unlisted"


def _default_media_factory(path: Path):
    from googleapiclient.http import MediaFileUpload
    return MediaFileUpload(str(path), chunksize=-1, resumable=True)


def upload_video(file_path: Path, metadata: VideoMetadata, *, youtube_client,
                 media_factory=None, thumbnail_path=None) -> str:
    factory = media_factory or _default_media_factory
    media = factory(file_path)
    body = {
        "snippet": {
            "title": metadata.title,
            "description": metadata.description,
            "tags": metadata.tags,
            "categoryId": "29",  # Nonprofits & Activism
        },
        "status": {"privacyStatus": metadata.privacy, "selfDeclaredMadeForKids": False},
    }
    request = youtube_client.videos().insert(part="snippet,status", body=body,
                                             media_body=media)
    response = request.execute()
    video_id = response["id"]

    # Set the title card as the custom thumbnail; otherwise YouTube auto-picks a
    # frame and often lands on the intro->sermon dissolve (an ugly blend).
    if thumbnail_path is not None and Path(thumbnail_path).exists():
        youtube_client.thumbnails().set(
            videoId=video_id, media_body=factory(thumbnail_path)
        ).execute()

    return f"https://youtu.be/{video_id}"


def write_upload_receipt(workdir: Path, video_id: str, url: str) -> Path:
    """Persist a small upload.json receipt (video_id + url) into workdir."""
    p = Path(workdir) / "upload.json"
    p.write_text(json.dumps({"video_id": video_id, "url": url}, indent=2))
    return p


def load_upload_receipt(workdir: Path) -> dict | None:
    """Load the upload.json receipt, or None if the run hasn't uploaded yet."""
    p = Path(workdir) / "upload.json"
    return json.loads(p.read_text()) if p.exists() else None


def format_shorts_title(candidate_title, sermon_title):
    """Format a shorts title as '<candidate> — <sermon>'."""
    return f"{candidate_title} — {sermon_title}"


def build_shorts_description(candidate_title, rationale, footer, *, sermon_url=None):
    """Build a shorts description with rationale, #Shorts, optional full sermon link, and footer."""
    parts = [rationale.strip(), "#Shorts"]
    if sermon_url:
        parts.insert(1, f"Full sermon: {sermon_url}")
    if footer.strip():
        parts.append(footer.strip())
    return "\n\n".join(p for p in parts if p)
