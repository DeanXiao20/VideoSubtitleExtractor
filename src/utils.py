import re
from pathlib import Path


def slugify_title(title: str, video_id: str, max_length: int = 60) -> str:
    """Convert a video title to a safe, readable filename.

    Appends a short hash from video_id for uniqueness.
    Example: "黄仁勋演讲" with video_id "d19ae4b9a3cb" → "黄仁勋演讲-d19ae4"
    """
    if not title or not title.strip():
        return video_id[:12] if video_id else "output"

    slug = title.strip()

    # Remove characters unsafe for filenames (Windows + cross-platform)
    # Keep: letters, digits, CJK, spaces, hyphens, underscores, dots
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', slug)
    # Replace multiple spaces with single
    slug = re.sub(r'\s+', ' ', slug)
    # Replace spaces with hyphens for cleaner URLs
    slug = slug.replace(' ', '-')

    # Truncate to max_length, leaving room for the suffix
    suffix = f"-{video_id[:6]}" if video_id else ""
    max_slug = max_length - len(suffix)
    if len(slug) > max_slug:
        slug = slug[:max_slug].rstrip('-')

    # Remove trailing hyphens
    slug = slug.rstrip('-')

    if not slug:
        return video_id[:12] if video_id else "output"

    return slug + suffix
