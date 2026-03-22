import asyncio
import json
import logging
import re
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any

import uvloop

from .config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

AMOUNT_PATTERN = re.compile(r"(?:Количество:|Итого:)\s*([0-9]+(?:[.,][0-9]+)?)", re.UNICODE)
CHANNEL_IDS_SET = settings.channel_ids_set

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


@dataclass
class RuntimeConfig:
    running: bool = False
    min_limit: float = 15.0
    max_limit: float | None = None


def signal_handler(sig, frame):
    logger.info("Shutting down...")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def extract_usdt_amount(text: str):
    m = AMOUNT_PATTERN.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def is_channel_post(message) -> bool:
    sender_id = message.sender_id
    if sender_id in CHANNEL_IDS_SET:
        return True

    fwd_from = message.fwd_from
    if fwd_from:
        channel_id = fwd_from.channel_id
        if channel_id in CHANNEL_IDS_SET:
            return True

        from_id = fwd_from.from_id
        if from_id and from_id.channel_id in CHANNEL_IDS_SET:
            return True

        saved_from_peer = fwd_from.saved_from_peer
        if saved_from_peer and saved_from_peer.channel_id in CHANNEL_IDS_SET:
            return True

    reply_header = message.reply_to
    if not reply_header:
        return False

    reply_peer_id = reply_header.reply_to_peer_id
    return bool(reply_peer_id and reply_peer_id.channel_id in CHANNEL_IDS_SET)


def contains_trigger_symbol(text: str) -> bool:
    return "₽" in text


def now_ts() -> float:
    return time.time()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def from_json(data: str | None, default: Any):
    if not data:
        return default
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return default
