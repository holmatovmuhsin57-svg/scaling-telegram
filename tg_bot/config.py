import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    discussion_chat_id: int
    channel_id: int
    owner_id: int
    fast_reply_text: str
    cmd_delete_delay: float
    rate_window_sec: int
    first_check_delay: float
    first_scan_limit: int
    stats_db_file: str
    stats_photo_file: str
    fast_session_name: str
    admin_session_name: str
    keepalive_interval_sec: int
    cfg_poll_interval_sec: float

    @property
    def command_chat_ids(self) -> set[int]:
        return {self.discussion_chat_id, self.owner_id}

    @property
    def channel_ids_set(self) -> set[int]:
        channel_id_abs = abs(self.channel_id)
        return {self.channel_id, channel_id_abs, channel_id_abs - 10**12}


settings = Settings(
    api_id=int(os.getenv("API_ID", "26437642")),
    api_hash=os.getenv("API_HASH", "7b265c727eb7d25531b83aba5964ebfa"),
    discussion_chat_id=int(os.getenv("DISCUSSION_CHAT_ID", "-1002820652138")),
    channel_id=int(os.getenv("CHANNEL_ID", "-1002744525041")),
    owner_id=int(os.getenv("OWNER_ID", "6911990381")),
    fast_reply_text=os.getenv("FAST_REPLY_TEXT", "я"),
    cmd_delete_delay=float(os.getenv("CMD_DELETE_DELAY", "1.0")),
    rate_window_sec=int(os.getenv("RATE_WINDOW_SEC", "60")),
    first_check_delay=float(os.getenv("FIRST_CHECK_DELAY", "0.15")),
    first_scan_limit=int(os.getenv("FIRST_SCAN_LIMIT", "100")),
    stats_db_file=os.getenv("BOT_DB_FILE", "bot_state.sqlite3"),
    stats_photo_file=os.getenv("STATS_PHOTO_FILE", "stats_photo.json"),
    fast_session_name=os.getenv("FAST_SESSION_NAME", "fast_session"),
    admin_session_name=os.getenv("ADMIN_SESSION_NAME", "admin_session"),
    keepalive_interval_sec=int(os.getenv("KEEPALIVE_INTERVAL_SEC", "25")),
    cfg_poll_interval_sec=float(os.getenv("CFG_POLL_INTERVAL_SEC", "0.5")),
)
