import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
WEB = ROOT / "web"
CACHE = DATA / "cache"
LLM_CACHE = CACHE / "llm"
RULES_DIR = DATA / "rules"
EVAL_DIR = DATA / "eval"
STATE_FILE = DATA / "state.json"
DATASET_FILE = DATA / "offers" / "dataset.json"

FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY", "").strip()
FEATHERLESS_BASE_URL = os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1").strip()
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY", "").strip()
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://eu.i.posthog.com").strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

MODEL_EXTRACT = os.getenv("MODEL_EXTRACT", "Qwen/Qwen3-30B-A3B-Instruct-2507").strip()
MODEL_REFLECT = os.getenv("MODEL_REFLECT", "deepseek-ai/DeepSeek-V3.2").strip()
EVAL_WORKERS = int(os.getenv("EVAL_WORKERS", "2"))

for d in (LLM_CACHE, RULES_DIR, EVAL_DIR):
    d.mkdir(parents=True, exist_ok=True)


def load_mission() -> dict:
    return json.loads((DATA / "mission.json").read_text())


def load_dataset() -> dict:
    return json.loads(DATASET_FILE.read_text())
