from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import MODELS_DIR, WHISPER_MODEL_SIZE, HF_MIRROR_URL

import os
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = HF_MIRROR_URL


def _get_local_model_path(model_size: str) -> Path | None:
    local_dir = MODELS_DIR / f"faster-whisper-{model_size}"
    if local_dir.exists() and (local_dir / "model.bin").exists():
        return local_dir
    return None


def _download_model_from_mirror(model_size: str) -> Path:
    import requests
    import time
    local_dir = MODELS_DIR / f"faster-whisper-{model_size}"
    local_dir.mkdir(parents=True, exist_ok=True)

    base_url = f"{HF_MIRROR_URL}/Systran/faster-whisper-{model_size}/resolve/main"
    small_files = ["config.json", "tokenizer.json", "vocabulary.txt"]

    for fname in small_files:
        out_path = local_dir / fname
        if out_path.exists() and out_path.stat().st_size > 0:
            continue
        url = f"{base_url}/{fname}"
        for attempt in range(5):
            try:
                resp = requests.get(url, timeout=60)
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2)

    model_path = local_dir / "model.bin"
    if not (model_path.exists() and model_path.stat().st_size > 400_000_000):
        url = f"{base_url}/model.bin"
        for attempt in range(20):
            try:
                existing = model_path.stat().st_size if model_path.exists() else 0
                headers = {"Range": f"bytes={existing}-"} if existing > 0 else {}
                resp = requests.get(url, headers=headers, stream=True, timeout=120)
                if resp.status_code == 200:
                    existing = 0
                mode = "ab" if existing > 0 and resp.status_code == 206 else "wb"
                with open(model_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)
                break
            except Exception:
                if attempt == 19:
                    raise
                time.sleep(3)

    return local_dir


@dataclass
class Segment:
    start: float
    end: float
    text: str


class Transcriber:
    def __init__(
        self,
        model_size: str = WHISPER_MODEL_SIZE,
        device: str = "cpu",
        compute_type: str = "int8",
        model_dir: Path | None = None,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model_dir = str(model_dir or MODELS_DIR)
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return

        from faster_whisper import WhisperModel

        local_path = _get_local_model_path(self.model_size)
        if local_path is not None:
            self._model = WhisperModel(
                str(local_path),
                device=self.device,
                compute_type=self.compute_type,
            )
            return

        try:
            os.environ["HF_ENDPOINT"] = HF_MIRROR_URL
            os.environ["HUGGINGFACE_HUB_URL"] = HF_MIRROR_URL
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.model_dir,
            )
        except Exception:
            local_path = _download_model_from_mirror(self.model_size)
            self._model = WhisperModel(
                str(local_path),
                device=self.device,
                compute_type=self.compute_type,
            )

    def transcribe(
        self,
        audio_path: Path | str,
        language: str | None = None,
        beam_size: int = 5,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> list[Segment]:
        self._load_model()
        if progress_callback:
            progress_callback(0.0, "正在加载模型并转录音频...")

        segments_iter, info = self._model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            word_timestamps=False,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
        )

        segments: list[Segment] = []
        total_duration = info.duration if info.duration > 0 else 1.0

        for seg in segments_iter:
            segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip()))
            if progress_callback and total_duration > 0:
                progress = min(seg.end / total_duration, 1.0)
                progress_callback(progress, f"转录中: {seg.end:.0f}s / {total_duration:.0f}s")

        if progress_callback:
            progress_callback(1.0, "转录完成")

        return segments

    def transcribe_to_dicts(
        self,
        audio_path: Path | str,
        language: str | None = None,
        beam_size: int = 5,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> list[dict]:
        segments = self.transcribe(audio_path, language, beam_size, progress_callback)
        return [
            {
                "start_time": s.start,
                "end_time": s.end,
                "text_original": s.text,
                "source_type": "transcribed",
            }
            for s in segments
        ]

    def unload_model(self) -> None:
        self._model = None
