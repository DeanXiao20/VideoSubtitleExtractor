import hashlib
import re
from pathlib import Path
from typing import Callable

import yt_dlp

from config import DOWNLOADS_DIR


class UnsupportedURLError(Exception):
    pass


class NetworkError(Exception):
    pass


class VideoNotAvailableError(Exception):
    pass


def detect_platform(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "bilibili.com" in url or "b23.tv" in url:
        return "bilibili"
    return "other"


def is_direct_media_url(url: str) -> bool:
    lower = url.lower()
    if any(lower.endswith(ext) or ext + "?" in lower for ext in (".mp4", ".mp3", ".wav", ".m4a", ".webm", ".flv", ".ogg")):
        return True
    if "mime_type=video" in lower or "mime_type=audio" in lower:
        return True
    if "toutiaovod.com" in lower or "bytevcloud.com" in lower:
        return True
    return False


def extract_video_id(url: str, platform: str) -> str | None:
    if platform == "youtube":
        m = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
        return m.group(1) if m else None
    if platform == "bilibili":
        m = re.search(r"/(BV[a-zA-Z0-9]+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/av(\d+)", url)
        return f"av{m.group(1)}" if m else None
    return None


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _safe_dir_name(video_id: str | None, url: str) -> str:
    if video_id:
        return video_id
    return _url_hash(url)


def _build_ydl_opts(proxy: str | None = None, **extra) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    if proxy:
        opts["proxy"] = proxy
    opts.update(extra)
    return opts


def get_video_info(url: str, proxy: str | None = None) -> dict:
    opts = _build_ydl_opts(proxy)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "not found" in msg.lower() or "does not exist" in msg.lower():
            raise VideoNotAvailableError(f"视频不存在或已被删除: {msg}")
        if "private" in msg.lower() or "sign in" in msg.lower():
            raise VideoNotAvailableError(f"视频为私密视频或需要登录: {msg}")
        raise NetworkError(f"网络错误: {msg}")
    except Exception as e:
        raise NetworkError(f"获取视频信息失败: {e}")

    platform = detect_platform(url)
    vid = extract_video_id(url, platform) or _url_hash(url)
    subs = info.get("subtitles", {})
    auto_caps = info.get("automatic_captions", {})
    available_langs = list(set(list(subs.keys()) + list(auto_caps.keys())))

    title = info.get("title", "")
    if not title or title == vid or (is_direct_media_url(url) and len(title) < 5):
        title = f"视频 {vid}"

    return {
        "url": url,
        "platform": platform,
        "video_id": vid,
        "title": info.get("title", ""),
        "author": info.get("uploader", info.get("channel", "")),
        "description": (info.get("description") or "")[:2000],
        "duration_seconds": info.get("duration"),
        "thumbnail_url": info.get("thumbnail", ""),
        "subtitles_available": bool(subs),
        "auto_captions_available": bool(auto_caps),
        "available_subtitle_languages": available_langs,
    }


def download_subtitles(
    url: str,
    output_dir: Path,
    languages: list[str] | None = None,
    auto_generated: bool = True,
    proxy: str | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_template = str(output_dir / "subtitle.%(ext)s")
    opts = _build_ydl_opts(proxy,
        skip_download=True,
        writesubtitles=True,
        writeautomaticsub=auto_generated,
        subtitlesformat="srt",
        outtmpl=safe_template,
    )
    if languages:
        opts["subtitleslangs"] = languages

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        raise NetworkError(f"下载字幕失败: {e}")

    result: dict[str, Path] = {}
    for f in output_dir.glob("*.srt"):
        lang_match = re.search(r"\.([a-z]{2}(?:-[A-Za-z]+)?)\.srt$", f.name)
        lang = lang_match.group(1) if lang_match else "unknown"
        result[lang] = f
    return result


def download_audio(
    url: str,
    output_dir: Path,
    proxy: str | None = None,
    progress_hook: Callable | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_template = str(output_dir / "audio.%(ext)s")
    opts = _build_ydl_opts(proxy,
        format="bestaudio/best",
        outtmpl=safe_template,
    )
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            p = Path(filename)
            if p.exists():
                return p
            # Fallback: check for the actual output file
            for f in output_dir.glob("audio*"):
                if f.suffix in (".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg"):
                    return f
            return p
    except yt_dlp.utils.DownloadError as e:
        # If ffmpeg postprocessing failed, try to find the downloaded file anyway
        for f in output_dir.glob("audio*"):
            if f.suffix in (".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg"):
                return f
        raise NetworkError(f"下载音频失败: {e}")


def download_direct_media(
    url: str,
    output_dir: Path,
    proxy: str | None = None,
) -> Path:
    import requests
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "media.mp4"

    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(url, stream=True, proxies=proxies, timeout=120)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path
    except Exception as e:
        raise NetworkError(f"下载媒体文件失败: {e}")


def parse_srt(file_path: Path) -> list[dict]:
    import pysrt
    subs = pysrt.open(str(file_path), encoding="utf-8")
    segments = []
    for sub in subs:
        start = sub.start.hours * 3600 + sub.start.minutes * 60 + sub.start.seconds + sub.start.milliseconds / 1000
        end = sub.end.hours * 3600 + sub.end.minutes * 60 + sub.end.seconds + sub.end.milliseconds / 1000
        text = sub.text.replace("\n", " ").strip()
        if text:
            segments.append({
                "start_time": start,
                "end_time": end,
                "text_original": text,
                "source_type": "extracted",
            })
    return segments


def parse_vtt(file_path: Path) -> list[dict]:
    content = file_path.read_text(encoding="utf-8")
    segments = []
    time_re = re.compile(r"(\d+):(\d+):(\d+)\.(\d+)\s*-->\s*(\d+):(\d+):(\d+)\.(\d+)")
    for block in content.split("\n\n"):
        lines = block.strip().splitlines()
        if not lines:
            continue
        matched_line_idx = 0
        m = time_re.search(lines[0] if lines else "")
        if not m and len(lines) > 1:
            m = time_re.search(lines[1])
            matched_line_idx = 1
        if not m:
            continue
        start = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/1000
        end = int(m.group(5))*3600 + int(m.group(6))*60 + int(m.group(7)) + int(m.group(8))/1000
        text_lines = lines[matched_line_idx + 1:]
        text = " ".join(l.strip() for l in text_lines if l.strip() and not l.startswith("WEBVTT"))
        if text:
            segments.append({
                "start_time": start,
                "end_time": end,
                "text_original": text,
                "source_type": "extracted",
            })
    return segments


def parse_subtitle_file(file_path: Path) -> list[dict]:
    if file_path.suffix == ".srt":
        return parse_srt(file_path)
    if file_path.suffix == ".vtt":
        return parse_vtt(file_path)
    return parse_srt(file_path)
