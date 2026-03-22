import asyncio
import time
from collections import deque

from telethon import TelegramClient, events
from telethon.network.connection import ConnectionTcpAbridged
from telethon.tl.functions.updates import GetStateRequest

from .config import settings
from .shared import RuntimeConfig, extract_usdt_amount, logger
from .storage import DEFAULT_STATS, Storage

client = TelegramClient(
    settings.admin_session_name,
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
stats = storage.load_stats()
rate = deque()
photo_media = None


class DeleteKind:
    SINGLE = 1
    PAIR_DELAYED = 2


delete_q = asyncio.Queue(maxsize=2000)
first_q = asyncio.Queue(maxsize=300)


def persist_state():
    storage.save_runtime_config(runtime_cfg)
    storage.save_stats(stats)


async def delete_worker():
    logger.info("Delete worker started")
    while True:
        kind, payload = await delete_q.get()
        try:
            if kind == DeleteKind.PAIR_DELAYED:
                chat_id, msg1, msg2, delay = payload
                await asyncio.sleep(delay)
                await client.delete_messages(chat_id, [msg1, msg2])
            else:
                chat_id, msg = payload
                await client.delete_messages(chat_id, [msg])
        except Exception as exc:
            logger.error("Delete error: %s", exc)
        finally:
            delete_q.task_done()


async def first_check_worker():
    logger.info("First check worker started")
    while True:
        chat_id, orig_id, our_reply_id, amount = await first_q.get()
        try:
            await asyncio.sleep(settings.first_check_delay)
            is_first = True
            async for m in client.iter_messages(chat_id, max_id=our_reply_id - 1, limit=settings.first_scan_limit):
                rt = m.reply_to
                if rt is not None and rt.reply_to_msg_id == orig_id:
                    is_first = False
                    break
                if m.id <= orig_id:
                    break
            if is_first:
                stats["first_cnt"] += 1
                stats["first_sum"] += amount
                storage.save_stats(stats)
        except Exception as exc:
            logger.error("First check error: %s", exc)
        finally:
            first_q.task_done()


async def get_stats_media():
    global photo_media
    if photo_media is not None:
        return photo_media

    photo = storage.load_photo()
    chat_id = photo.get("chat_id")
    msg_id = photo.get("msg_id")
    if not chat_id or not msg_id:
        return None
    try:
        msg = await client.get_messages(chat_id, ids=msg_id)
        if msg and msg.media:
            photo_media = msg.media
            return msg.media
    except Exception as exc:
        logger.error("Stats media error: %s", exc)
    return None


async def live_ping_ms():
    t0 = time.perf_counter()
    try:
        await client(GetStateRequest())
        return (time.perf_counter() - t0) * 1000.0
    except Exception as exc:
        logger.error("Ping error: %s", exc)
        return -1.0


def stats_text(ping: float):
    now = time.time()
    total = stats["total"]
    rpm = len(rate) * (60.0 / settings.rate_window_sec)
    ago_s = f"{int(now - stats['last_reply_ts'])}s" if stats["last_reply_ts"] else "-"

    def f3(x):
        return "-" if x is None else f"{x:.3f}"

    def f4(x):
        return "-" if x is None else f"{x:.4f}"

    return (
        "📊 Stats\n"
        f"⚡ Ping: {ping:.1f} ms | RPM: {rpm:.1f} | Last: {ago_s}\n"
        f"⏱️ ms last {stats['last_ms']:.3f} | avg {stats['avg_ms']:.3f} | best {f3(stats['best_ms'])} | worst {f3(stats['worst_ms'])}\n"
        f"✅ Win: {stats['first_sum']:.4f} USDT ({stats['first_cnt']}, {(stats['first_cnt'] / total * 100.0) if total else 0.0:.1f}%)\n"
        f"💵 Sum: {stats['sum_usdt']:.4f} | min {f4(stats['min_usdt'])} | max {f4(stats['max_usdt'])} | last {f4(stats['last_usdt'])}\n"
        f"📌 Limits: min {f4(runtime_cfg.min_limit)} | max {f4(runtime_cfg.max_limit) if runtime_cfg.max_limit is not None else '-'}\n"
        f"📈 Total: {total} | Err: {stats['errors']}\n"
        f"🧾 Skip: p{stats['skip_parse']} min{stats['skip_min']} max{stats['skip_max']} | Seen: {stats['seen_usdt']}\n"
        f"🤖 Running: {'Yes' if runtime_cfg.running else 'No'}"
    )


async def send_stats(chat_id: int, reply_to_id: int):
    ping = await live_ping_ms()
    txt = stats_text(ping)
    media = await get_stats_media()
    if media:
        try:
            await client.send_file(chat_id, media, caption=txt, reply_to=reply_to_id)
            return
        except Exception as exc:
            logger.error("Send stats file error: %s", exc)
    await client.send_message(chat_id, txt, reply_to=reply_to_id, parse_mode=None)


async def verify_loop():
    logger.info("Verify loop started")
    global stats
    while True:
        event = storage.claim_event()
        if not event:
            await asyncio.sleep(0.03)
            continue
        try:
            stats = storage.load_stats()
            chat_id = event["chat_id"]
            orig_id = event["orig_id"]
            sent_id = event["sent_id"]
            text = event["text"]
            dt_ns = event["dt_ns"]

            stats["seen_usdt"] += 1
            amount = extract_usdt_amount(text)
            if amount is None:
                stats["skip_parse"] += 1
                await delete_q.put((DeleteKind.SINGLE, (chat_id, sent_id)))
                storage.save_stats(stats)
                storage.ack_event(event["id"])
                continue

            mn = runtime_cfg.min_limit
            if mn is not None and amount < mn:
                stats["skip_min"] += 1
                await delete_q.put((DeleteKind.SINGLE, (chat_id, sent_id)))
                storage.save_stats(stats)
                storage.ack_event(event["id"])
                continue

            mx = runtime_cfg.max_limit
            if mx is not None and amount > mx:
                stats["skip_max"] += 1
                await delete_q.put((DeleteKind.SINGLE, (chat_id, sent_id)))
                storage.save_stats(stats)
                storage.ack_event(event["id"])
                continue

            dt = dt_ns * 1e-6
            stats["total"] += 1
            total = stats["total"]
            stats["last_ms"] = dt
            stats["last_reply_ts"] = time.time()
            if stats["best_ms"] is None or dt < stats["best_ms"]:
                stats["best_ms"] = dt
            if stats["worst_ms"] is None or dt > stats["worst_ms"]:
                stats["worst_ms"] = dt
            stats["avg_ms"] = dt if total == 1 else (stats["avg_ms"] * (total - 1) + dt) / total
            stats["sum_usdt"] += amount
            stats["last_usdt"] = amount
            if stats["min_usdt"] is None or amount < stats["min_usdt"]:
                stats["min_usdt"] = amount
            if stats["max_usdt"] is None or amount > stats["max_usdt"]:
                stats["max_usdt"] = amount
            now = time.time()
            rate.append(now)
            cutoff = now - settings.rate_window_sec
            while rate and rate[0] < cutoff:
                rate.popleft()
            storage.save_stats(stats)
            try:
                first_q.put_nowait((chat_id, orig_id, sent_id, amount))
            except asyncio.QueueFull:
                pass
            storage.ack_event(event["id"])
        except Exception as exc:
            logger.error("Verify loop error: %s", exc)
            storage.release_event(event["id"])
            await asyncio.sleep(0.2)


@client.on(events.NewMessage(from_users=settings.owner_id))
async def command_handler(event):
    global runtime_cfg, photo_media
    text = (event.raw_text or "").strip()
    if not text.startswith("/"):
        return
    if event.chat_id not in settings.command_chat_ids:
        return

    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]
    reply = None
    delete_pair = True

    if cmd == "/start_bot":
        runtime_cfg.running = True
        storage.save_runtime_config(runtime_cfg)
        reply = await event.reply("OK")
    elif cmd == "/stop_bot":
        runtime_cfg.running = False
        storage.save_runtime_config(runtime_cfg)
        reply = await event.reply("STOP")
    elif cmd == "/status":
        reply = await event.reply("ON" if runtime_cfg.running else "OFF")
    elif cmd == "/set_min":
        if args:
            try:
                runtime_cfg.min_limit = float(args[0].replace(",", "."))
                storage.save_runtime_config(runtime_cfg)
                reply = await event.reply(f"Min {runtime_cfg.min_limit}")
            except Exception:
                reply = await event.reply("Error")
        else:
            reply = await event.reply("Usage: /set_min 20")
    elif cmd == "/set_max":
        if args:
            try:
                value = float(args[0].replace(",", "."))
                runtime_cfg.max_limit = None if value == 0 else value
                storage.save_runtime_config(runtime_cfg)
                reply = await event.reply(f"Max {runtime_cfg.max_limit if runtime_cfg.max_limit is not None else '-'}")
            except Exception:
                reply = await event.reply("Error")
        else:
            reply = await event.reply("Usage: /set_max 300 (0=off)")
    elif cmd == "/limits":
        reply = await event.reply(f"Min {runtime_cfg.min_limit}\nMax {runtime_cfg.max_limit if runtime_cfg.max_limit is not None else '-'}")
    elif cmd == "/stats":
        asyncio.create_task(send_stats(event.chat_id, event.message.id))
        delete_pair = False
    elif cmd == "/set_stats_photo":
        if not event.is_reply:
            reply = await event.reply("Reply to photo: /set_stats_photo")
            delete_pair = False
        else:
            r = await event.get_reply_message()
            if not r or not r.media:
                reply = await event.reply("No media")
                delete_pair = False
            else:
                storage.save_photo({"chat_id": event.chat_id, "msg_id": r.id})
                photo_media = r.media
                reply = await event.reply("Saved")
    elif cmd == "/clear_stats_photo":
        storage.save_photo({"chat_id": None, "msg_id": None})
        photo_media = None
        reply = await event.reply("Cleared")
    else:
        return

    if reply and delete_pair:
        try:
            delete_q.put_nowait((DeleteKind.PAIR_DELAYED, (event.chat_id, event.message.id, reply.id, settings.cmd_delete_delay)))
        except asyncio.QueueFull:
            pass


async def main():
    logger.info("Starting backoffice...")
    await client.start()
    client.loop.create_task(delete_worker())
    client.loop.create_task(first_check_worker())
    client.loop.create_task(verify_loop())
    logger.info("Backoffice ready")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
