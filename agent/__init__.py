"""Agent package.

Loads the project-level .env file into os.environ at import time so API keys
work without manual shell setup. Real environment variables always WIN over
.env values. Secrets stay out of source code.
"""
import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    path = os.path.join(_BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:   # real env takes priority
                os.environ[key] = value


_load_dotenv()
