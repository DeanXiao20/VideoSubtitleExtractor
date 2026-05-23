import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.cefr_loader import CEFRLookup
from src.translator import Translator
from config import CEFR_DIFFICULTY_THRESHOLD, DATA_DIR

_SKIP_WORDS = frozenset({
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "this", "that",
    "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "a", "an", "the", "and", "but", "or", "so", "if", "as",
    "at", "by", "for", "in", "of", "on", "to", "with", "from", "into",
    "not", "no", "nor", "up", "out", "off", "then", "than",
    "can", "will", "would", "could", "should", "may", "might",
    "what", "which", "who", "whom", "how", "when", "where", "why",
    "here", "there", "all", "each", "every", "both", "few", "many",
    "some", "any", "more", "most", "other", "such",
    "just", "very", "also", "too", "now",
})

_PHRASE_DICT: dict[str, str] | None = None


def _load_phrase_dict() -> dict[str, str]:
    global _PHRASE_DICT
    if _PHRASE_DICT is not None:
        return _PHRASE_DICT
    path = DATA_DIR / "cefr" / "phrases.json"
    if path.exists():
        try:
            _PHRASE_DICT = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _PHRASE_DICT = {}
    else:
        _PHRASE_DICT = {}
    return _PHRASE_DICT


@dataclass
class WordAnnotation:
    word: str
    ipa: str
    cefr_level: str
    chinese_definition: str
    start_pos: int = 0
    end_pos: int = 0


@dataclass
class PhraseAnnotation:
    phrase: str
    chinese_definition: str
    start_pos: int = 0
    end_pos: int = 0


def _get_ipa(word: str) -> str:
    try:
        from eng_to_ipa import convert
        ipa = convert(word, keep_punct=False, stress_marks="primary")
        if "*" in ipa:
            return ""
        return ipa
    except Exception:
        return ""


def _simple_lemma(word: str) -> str:
    w = word.lower()
    for suffix in ("ing", "ed", "ly", "tion", "sion", "ment", "ness", "ity", "ous", "ive", "able", "ible"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    if w.endswith("es") and len(w) > 4:
        return w[:-2]
    return w


def _find_phrases(text: str, phrase_dict: dict[str, str]) -> list[PhraseAnnotation]:
    text_lower = text.lower()
    found: list[PhraseAnnotation] = []
    used_ranges: list[tuple[int, int]] = []

    for phrase, definition in phrase_dict.items():
        phrase_lower = phrase.lower()
        phrase_words = phrase_lower.split()
        if len(phrase_words) < 2:
            continue

        start = 0
        while True:
            pos = text_lower.find(phrase_lower, start)
            if pos == -1:
                break
            end = pos + len(phrase_lower)

            overlaps = any(not (end <= s or pos >= e) for s, e in used_ranges)
            if not overlaps and len(phrase_words) >= 2:
                found.append(PhraseAnnotation(
                    phrase=text[pos:end],
                    chinese_definition=definition,
                    start_pos=pos,
                    end_pos=end,
                ))
                used_ranges.append((pos, end))
            start = pos + 1

    found.sort(key=lambda p: p.start_pos)
    return found


def annotate_segment(
    english_text: str,
    cefr_lookup: CEFRLookup,
    translator: Translator,
    threshold: str = CEFR_DIFFICULTY_THRESHOLD,
) -> tuple[list[WordAnnotation], list[PhraseAnnotation]]:
    phrase_dict = _load_phrase_dict()
    phrase_annotations = _find_phrases(english_text, phrase_dict)

    phrase_char_ranges: set[int] = set()
    for pa in phrase_annotations:
        for i in range(pa.start_pos, pa.end_pos):
            phrase_char_ranges.add(i)

    words = re.finditer(r"[a-zA-Z']+", english_text)
    difficult_words: list[str] = []
    word_positions: list[tuple[str, int, int]] = []

    for m in words:
        word = m.group()
        lower = word.lower()
        if lower in _SKIP_WORDS or len(lower) <= 1:
            continue

        in_phrase = all(i in phrase_char_ranges for i in range(m.start(), m.end()))
        if in_phrase:
            continue

        if cefr_lookup.is_difficult(lower, threshold):
            difficult_words.append(lower)
            word_positions.append((lower, m.start(), m.end()))
        else:
            lemma = _simple_lemma(lower)
            if lemma != lower and cefr_lookup.is_difficult(lemma, threshold):
                difficult_words.append(lower)
                word_positions.append((lower, m.start(), m.end()))

    if difficult_words:
        unique_words = list(set(difficult_words))
        definitions = translator.translate_word_definitions(unique_words)
    else:
        definitions = {}

    annotations: list[WordAnnotation] = []
    for word, start, end in word_positions:
        ipa = _get_ipa(word)
        level = cefr_lookup.get_level(word)
        if level is None:
            lemma = _simple_lemma(word)
            level = cefr_lookup.get_level(lemma)
        chinese_def = definitions.get(word.lower(), "")
        annotations.append(WordAnnotation(
            word=word,
            ipa=ipa,
            cefr_level=level or "Unknown",
            chinese_definition=chinese_def,
            start_pos=start,
            end_pos=end,
        ))

    return annotations, phrase_annotations


def annotate_all_segments(
    segments: list[dict],
    cefr_lookup: CEFRLookup,
    translator: Translator,
    threshold: str = CEFR_DIFFICULTY_THRESHOLD,
    progress_callback: Callable[[float, str], None] | None = None,
) -> list[dict]:
    all_difficult_words: set[str] = set()
    for seg in segments:
        text = seg.get("text_original", "")
        for m in re.finditer(r"[a-zA-Z']+", text):
            word = m.group().lower()
            if word in _SKIP_WORDS or len(word) <= 1:
                continue
            if cefr_lookup.is_difficult(word, threshold):
                all_difficult_words.add(word)
            else:
                lemma = _simple_lemma(word)
                if lemma != word and cefr_lookup.is_difficult(lemma, threshold):
                    all_difficult_words.add(word)

    if all_difficult_words:
        translator.translate_word_definitions(
            list(all_difficult_words),
            progress_callback=progress_callback,
        )

    results: list[dict] = []
    total = len(segments)
    for i, seg in enumerate(segments):
        text = seg.get("text_original", "")
        word_anns, phrase_anns = annotate_segment(text, cefr_lookup, translator, threshold)
        new_seg = dict(seg)
        new_seg["annotations"] = [
            {
                "type": "word",
                "word": a.word,
                "ipa": a.ipa,
                "cefr_level": a.cefr_level,
                "chinese_definition": a.chinese_definition,
                "start_pos": a.start_pos,
                "end_pos": a.end_pos,
            }
            for a in word_anns
        ]
        new_seg["phrase_annotations"] = [
            {
                "type": "phrase",
                "phrase": p.phrase,
                "chinese_definition": p.chinese_definition,
                "start_pos": p.start_pos,
                "end_pos": p.end_pos,
            }
            for p in phrase_anns
        ]
        results.append(new_seg)
        if progress_callback:
            progress_callback((i + 1) / total, f"标注单词: {i + 1}/{total}")

    return results
