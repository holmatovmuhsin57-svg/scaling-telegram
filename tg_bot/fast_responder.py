import asyncio
import logging

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.network.connection import ConnectionTcpAbridged
from telethon.tl.functions.updates import GetStateRequest

from .config import settings
from .shared import contains_trigger_symbol, is_channel_post, logger
from .storage import Storage

client = TelegramClient(
    settings.fast_session_name,
    settings.api_id,
    settings.api_hash,
    connection=ConnectionTcpAbridged,
    use_ipv6=False,
    flood_sleep_threshold=0,
    sequential_updates=False,
)
client.parse_mode = None

storage = Storage()
runtime_cfg = storage.load_runtime_config()
discussion_peer = None
send_message_fast = client.send_message
perf_counter_ns = __import__("time").perf_counter_ns


async def config_poll_worker():
    global runtime_cfg
    while True:
        try:
            runtime_cfg = storage.load_runtime_config()
        except Exception as exc:
            logger.error("Config poll error: %s", exc)
        await asyncio.sleep(settings.cfg_poll_interval_sec)


async def keepalive_worker():
    logger.info("Fast responder keepalive started")
    while True:
        try:
            await client(GetStateRequest())
        except Exception as exc:
            logger.debug("Fast keepalive error: %s", exc)
        await asyncio.sleep(settings.keepalive_interval_sec)


@client.on(events.NewMessage(chats=settings.discussion_chat_id, incoming=True))
async def turbo_handler(event):
    if not runtime_cfg.running:
        return

    message = event.message
    text = event.raw_text
    if not text or not contains_trigger_symbol(text):
        return

    if not is_channel_post(message):
        return

    t0 = perf_counter_ns()
    try:
        peer = discussion_peer if discussion_peer is not None else event.chat_id
        sent = await send_message_fast(peer, settings.fast_reply_text, reply_to=message.id, link_preview=False, parse_mode=None)
        dt_ns = perf_counter_ns() - t0
        try:
            storage.enqueue_event(event.chat_id, message.id, sent.id, text, dt_ns)
        except Exception as exc:
            logger.error("Enqueue error: %s", exc)
    except FloodWaitError:
        stats = storage.load_stats()
        stats["errors"] += 1
        storage.save_stats(stats)
    except Exception as exc:
        stats = storage.load_stats()
        stats["errors"] += 1
        storage.save_stats(stats)
        logger.error("Error in fast responder: %s", exc)


async def main():
    global discussion_peer
    logger.info("Starting fast responder...")
    await client.start()
    discussion_peer = await client.get_input_entity(settings.discussion_chat_id)
    client.loop.create_task(config_poll_worker())
    client.loop.create_task(keepalive_worker())
    logger.info("Fast responder ready")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
