import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
OUTPUT_DIR = DATA_DIR / "output"
MODELS_DIR = DATA_DIR / "models"
DB_PATH = DATA_DIR / "app.db"
CEFR_DIR = DATA_DIR / "cefr"
WORD_CACHE_PATH = DATA_DIR / "word_cache.json"
SETTINGS_PATH = DATA_DIR / "settings.json"

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")

HTTP_PROXY = os.getenv("HTTP_PROXY", "")
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")

CEFR_DIFFICULTY_THRESHOLD = "A1"
CEFR_ORDER = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}

TRANSLATION_BATCH_SIZE = 50
TRANSLATION_DELAY_SECONDS = 1.0

SOURCE_LANG = "en"
TARGET_LANG = "zh-CN"

HF_MIRROR_URL = "https://hf-mirror.com"

APP_PORT = 8002

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

MAX_CLASSIC_SENTENCES = int(os.getenv("MAX_CLASSIC_SENTENCES", "20"))

CF_PAGES_PROJECT = os.getenv("CF_PAGES_PROJECT", "")
GITHUB_PAGES_REPO = os.getenv("GITHUB_PAGES_REPO", "")
