"""Shared identifier and timestamp helpers."""
import uuid
from datetime import datetime, timezone
from media_utils import clean_relative_path


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()
