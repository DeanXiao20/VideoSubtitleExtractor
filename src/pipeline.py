import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import (
    DOWNLOADS_DIR,
    OUTPUT_DIR,
    WHISPER_MODEL_SIZE,
    CEFR_DIFFICULTY_THRESHOLD,
    HF_MIRROR_URL,
)


def _probe_duration(media_path: Path) -> float | None:
    try:
        import av
        with av.open(str(media_path)) as container:
            if container.duration:
                return container.duration / 1000000
    except Exception:
        pass
    return None


@dataclass
class PipelineResult:
    html: str
    video_info: dict
    segments: list[dict]
    video_id: int


class Pipeline:
    def __init__(self, settings: dict | None = None):
        self.settings = settings or {}
        self._cefr_lookup = None
        self._translator = None
        self._transcriber = None
        self._db = None

    @property
    def proxy(self) -> str | None:
        p = self.settings.get("https_proxy") or self.settings.get("http_proxy") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        return p if p else None

    @property
    def proxy_dict(self) -> dict | None:
        p = self.proxy
        if not p:
            return None
        return {"http": p, "https": p}

    @property
    def whisper_model(self) -> str:
        return self.settings.get("whisper_model", WHISPER_MODEL_SIZE)

    @property
    def difficulty_threshold(self) -> str:
        return self.settings.get("difficulty_threshold", CEFR_DIFFICULTY_THRESHOLD)

    @property
    def use_hf_mirror(self) -> bool:
        return self.settings.get("use_hf_mirror", True)

    @property
    def cefr_lookup(self):
        if self._cefr_lookup is None:
            from src.cefr_loader import CEFRLookup
            self._cefr_lookup = CEFRLookup(threshold=self.difficulty_threshold)
        return self._cefr_lookup

    @property
    def translator(self):
        if self._translator is None:
            from src.translator import Translator
            self._translator = Translator(proxy=self.proxy_dict)
        return self._translator

    @property
    def transcriber(self):
        if self._transcriber is None:
            from src.transcriber import Transcriber
            self._transcriber = Transcriber(model_size=self.whisper_model)
        return self._transcriber

    @property
    def db(self):
        if self._db is None:
            from src.database import Database
            self._db = Database()
        return self._db

    def run(
        self,
        url: str,
        force_transcribe: bool = False,
        prefer_subtitles: bool = True,
        progress_callback: Callable[[str, float, str], None] | None = None,
    ) -> PipelineResult:
        def _progress(stage: str, fraction: float, text: str):
            if progress_callback:
                progress_callback(stage, fraction, text)

        # Step 1: Extract video info
        _progress("extract", 0.0, "正在获取视频信息...")
        from src.extractor import (
            get_video_info, download_subtitles, download_audio,
            download_direct_media, is_direct_media_url, parse_subtitle_file,
            _safe_dir_name, _url_hash,
        )
        from src.annotator import annotate_all_segments
        from src.html_generator import generate_bilingual_html

        is_direct = is_direct_media_url(url)
        if is_direct:
            vid_id = _url_hash(url)
            video_info = {
                "url": url,
                "platform": "direct",
                "video_id": vid_id,
                "title": f"视频 {vid_id}",
                "author": "",
                "description": "",
                "duration_seconds": None,
                "thumbnail_url": "",
                "subtitles_available": False,
                "auto_captions_available": False,
                "available_subtitle_languages": [],
            }
        else:
            video_info = get_video_info(url, proxy=self.proxy)
        vid_id = video_info.get("video_id", "tmp")
        safe_name = _safe_dir_name(vid_id, url)
        _progress("extract", 1.0, f"视频: {video_info.get('title', '未知')}")

        # Step 2: Get subtitles or transcribe
        segments: list[dict] = []
        available_langs = video_info.get("available_subtitle_languages", [])

        if prefer_subtitles and not force_transcribe and available_langs:
            _progress("subtitles", 0.0, "正在下载字幕...")
            dl_dir = DOWNLOADS_DIR / f"subs_{safe_name}"
            try:
                en_available = any(l.startswith("en") for l in available_langs)
                sub_files = download_subtitles(
                    url, dl_dir,
                    languages=["en", "zh-Hans", "zh", "zh-CN"] if not en_available else ["en"],
                    auto_generated=True,
                    proxy=self.proxy,
                )
                for lang, path in sub_files.items():
                    if lang.startswith("en"):
                        segments = parse_subtitle_file(path)
                        for s in segments:
                            s["language"] = "en"
                        break
                    elif lang.startswith("zh"):
                        segments = parse_subtitle_file(path)
                        for s in segments:
                            s["language"] = "zh"
                        break

                if not segments and sub_files:
                    first_path = next(iter(sub_files.values()))
                    segments = parse_subtitle_file(first_path)
                    for s in segments:
                        s["language"] = "unknown"

                _progress("subtitles", 1.0, f"已提取 {len(segments)} 条字幕")
            except Exception as e:
                _progress("subtitles", 0.5, f"字幕下载失败: {e}，尝试转录...")
                segments = []

        if not segments:
            _progress("transcribe", 0.0, "正在下载音频...")
            audio_dir = DOWNLOADS_DIR / f"audio_{safe_name}"
            try:
                if is_direct_media_url(url):
                    _progress("transcribe", 0.05, "检测到直接视频链接，正在下载...")
                    audio_path = download_direct_media(url, audio_dir, proxy=self.proxy)
                else:
                    audio_path = download_audio(url, audio_dir, proxy=self.proxy)
            except Exception as e:
                from src.extractor import NetworkError
                raise NetworkError(f"音频下载失败: {e}")

            if video_info.get("duration_seconds") is None:
                dur = _probe_duration(audio_path)
                if dur:
                    video_info["duration_seconds"] = int(dur)

            _progress("transcribe", 0.2, "正在转录音频（可能需要几分钟）...")
            segments = self.transcriber.transcribe_to_dicts(
                audio_path,
                language=None,
                progress_callback=lambda f, t: _progress("transcribe", 0.2 + f * 0.8, t),
            )
            # Detect language from first segment
            detected_lang = "en"
            for s in segments:
                text = s.get("text_original", "")
                if text and any('一' <= c <= '鿿' for c in text):
                    detected_lang = "zh"
                    break
            for s in segments:
                s["language"] = detected_lang
            _progress("transcribe", 1.0, f"转录完成: {len(segments)} 条")

        if not segments:
            raise ValueError("未能获取任何字幕或转录内容")

        # Step 3: Translate
        _progress("translate", 0.0, "正在翻译...")
        src_lang = segments[0].get("language", "en") if segments else "en"
        if src_lang.startswith("zh"):
            from src.translator import Translator as _T
            zh_translator = _T(source_lang="zh-CN", target_lang="en", proxy=self.proxy_dict)
            texts_to_translate = [s.get("text_original", "") for s in segments]
            translations = zh_translator.translate_batch(
                texts_to_translate,
                progress_callback=lambda f, t: _progress("translate", f, t),
            )
            for i, seg in enumerate(segments):
                # For Chinese source: put Chinese as original, English as translated
                seg["text_translated"] = translations[i] if i < len(translations) else ""
                seg["language"] = "zh"
        else:
            texts_to_translate = [s.get("text_original", "") for s in segments]
            translations = self.translator.translate_batch(
                texts_to_translate,
                progress_callback=lambda f, t: _progress("translate", f, t),
            )
            for i, seg in enumerate(segments):
                seg["text_translated"] = translations[i] if i < len(translations) else ""
        _progress("translate", 1.0, "翻译完成")

        # Step 4: Annotate difficult words
        _progress("annotate", 0.0, "正在标注生词...")
        segments = annotate_all_segments(
            segments,
            self.cefr_lookup,
            self.translator,
            threshold=self.difficulty_threshold,
            progress_callback=lambda f, t: _progress("annotate", f, t),
        )
        _progress("annotate", 1.0, "标注完成")

        # Step 5: Generate HTML
        _progress("generate", 0.0, "正在生成HTML...")
        output_path = OUTPUT_DIR / f"{video_info.get('video_id', 'output')}.html"
        html_content = generate_bilingual_html(video_info, segments, output_path)
        _progress("generate", 1.0, "HTML生成完成")

        # Step 6: Save to database
        _progress("save", 0.0, "正在保存...")
        vid = self.db.save_video(video_info)
        self.db.save_segments(vid, segments)
        _progress("save", 1.0, "保存完成")

        # Cleanup downloads
        try:
            dl_dir = DOWNLOADS_DIR
            if dl_dir.exists():
                for d in dl_dir.iterdir():
                    if d.is_dir() and (d.name.startswith("subs_") or d.name.startswith("audio_")):
                        shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

        return PipelineResult(
            html=html_content,
            video_info=video_info,
            segments=segments,
            video_id=vid,
        )
