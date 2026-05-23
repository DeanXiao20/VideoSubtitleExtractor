from pathlib import Path
from config import CEFR_DIR, CEFR_ORDER, CEFR_DIFFICULTY_THRESHOLD

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


class CEFRLookup:
    def __init__(self, cefr_dir: Path | None = None, threshold: str = CEFR_DIFFICULTY_THRESHOLD):
        self.cefr_dir = cefr_dir or CEFR_DIR
        self.threshold = threshold
        self._word_level: dict[str, str] = {}
        self._middle_school_words: set[str] = set()
        self._load_all()

    def _load_all(self) -> None:
        for level in CEFR_LEVELS:
            path = self.cefr_dir / f"CEFR_{level}.txt"
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    word = line.strip().lower()
                    if word and word not in self._word_level:
                        self._word_level[word] = level

        ms_path = self.cefr_dir / "初中.txt"
        if ms_path.exists():
            for line in ms_path.read_text(encoding="utf-8").splitlines():
                word = line.strip().lower()
                if word:
                    self._middle_school_words.add(word)

    def get_level(self, word: str) -> str | None:
        return self._word_level.get(word.lower())

    def is_difficult(self, word: str, threshold: str | None = None) -> bool:
        level = self.get_level(word)
        if level is None:
            return False
        thresh = threshold or self.threshold
        return CEFR_ORDER.get(level, 0) > CEFR_ORDER.get(thresh, 0)

    def is_middle_school_word(self, word: str) -> bool:
        return word.lower() in self._middle_school_words

    def get_difficult_words(self, text: str, threshold: str | None = None) -> list[dict]:
        import re
        words = re.findall(r"[a-zA-Z']+", text)
        seen: set[str] = set()
        results: list[dict] = []
        for word in words:
            lower = word.lower()
            if lower in seen:
                continue
            seen.add(lower)
            if self.is_difficult(lower, threshold):
                level = self.get_level(lower)
                results.append({
                    "word": lower,
                    "cefr_level": level or "Unknown",
                })
        return results

    def total_words(self) -> int:
        return len(self._word_level)
