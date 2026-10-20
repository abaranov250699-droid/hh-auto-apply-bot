import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "8688226659:AAFhIVbsq4gTwe3EGq_KBVfs5WM4CJYw_Wo")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DB_PATH = DATA_DIR / "hh_bot.db"
DEFAULT_CHECK_INTERVAL = 300  # 5 minutes
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
