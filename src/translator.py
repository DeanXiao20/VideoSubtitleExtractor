import json
import time
from pathlib import Path
from typing import Callable

from config import (
    WORD_CACHE_PATH,
    TRANSLATION_BATCH_SIZE,
    TRANSLATION_DELAY_SECONDS,
    SOURCE_LANG,
    TARGET_LANG,
)


class TranslationError(Exception):
    pass


class TranslationQuotaError(TranslationError):
    pass


class Translator:
    def __init__(
        self,
        source_lang: str = SOURCE_LANG,
        target_lang: str = TARGET_LANG,
        proxy: dict | None = None,
    ):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.proxy = proxy
        self._word_cache: dict[str, str] = {}
        self._load_cache()

    def _get_translator(self):
        from deep_translator import GoogleTranslator
        kwargs = {"source": self.source_lang, "target": self.target_lang}
        if self.proxy:
            kwargs["proxies"] = self.proxy
        return GoogleTranslator(**kwargs)

    def _load_cache(self) -> None:
        path = WORD_CACHE_PATH
        if path.exists():
            try:
                self._word_cache = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._word_cache = {}

    def _save_cache(self) -> None:
        path = WORD_CACHE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._word_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return ""
        try:
            translator = self._get_translator()
            result = translator.translate(text)
            return result or ""
        except Exception as e:
            raise TranslationError(f"翻译失败: {e}")

    def translate_batch(
        self,
        texts: list[str],
        batch_size: int = TRANSLATION_BATCH_SIZE,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> list[str]:
        results: list[str] = []
        total = len(texts)
        for i in range(0, total, batch_size):
            batch = texts[i:i + batch_size]
            for j, text in enumerate(batch):
                try:
                    results.append(self.translate_text(text))
                except TranslationError:
                    results.append("")
                if progress_callback:
                    done = i + j + 1
                    progress_callback(done / total, f"翻译中: {done}/{total}")
            if i + batch_size < total:
                time.sleep(TRANSLATION_DELAY_SECONDS)
        return results

    def translate_word_definitions(
        self,
        words: list[str],
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> dict[str, str]:
        uncached = [w for w in words if w.lower() not in self._word_cache]
        definitions: dict[str, str] = {}

        for i, word in enumerate(uncached):
            try:
                result = self.translate_text(word)
                definitions[word.lower()] = result
                self._word_cache[word.lower()] = result
            except TranslationError:
                definitions[word.lower()] = ""
                self._word_cache[word.lower()] = ""

            if progress_callback and uncached:
                progress_callback((i + 1) / len(uncached), f"翻译单词释义: {word}")

            if i < len(uncached) - 1:
                time.sleep(0.3)

        if uncached:
            self._save_cache()

        for word in words:
            lower = word.lower()
            if lower not in definitions and lower in self._word_cache:
                definitions[lower] = self._word_cache[lower]

        return definitions
