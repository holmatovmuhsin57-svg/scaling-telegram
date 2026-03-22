# Multi-service deployment for Telegram turbo responder

## Architecture
- `fast_responder.py`: the only process that listens to discussion posts and sends the immediate reply.
- `backoffice.py`: consumes post-reply events from the local SQLite queue, runs verify/delete/first-check/stats, and handles owner commands.
- `bot_state.sqlite3`: shared local IPC/state database in WAL mode. Fast responder only appends events and reads config snapshots; backoffice consumes events and updates config/stats.
- Separate Telethon sessions are mandatory:
  - `FAST_SESSION_NAME=fast_session`
  - `ADMIN_SESSION_NAME=admin_session`

## Why this is safe
- Only **one** process (`fast_responder.py`) subscribes to the hot discussion feed and actually replies, so there are no duplicate instant replies.
- The backoffice process does **not** listen to the discussion feed for turbo replies; it only handles owner commands and post-processing.
- SQLite is used only as local IPC/state storage with `WAL` enabled, so there is no shared Telethon session file and no `sqlite database is locked` on Telethon sessions.

## Project layout
- `/opt/tgbot/fast_responder.py`
- `/opt/tgbot/backoffice.py`
- `/opt/tgbot/tg_bot/`
- `/opt/tgbot/tgbot-fast.service.example`
- `/opt/tgbot/tgbot-backoffice.service.example`

## 1) Create user and copy project
```bash
sudo useradd --system --create-home --home-dir /opt/tgbot --shell /usr/sbin/nologin tgbot || true
sudo mkdir -p /opt/tgbot
sudo rsync -a --delete ./ /opt/tgbot/
sudo chown -R tgbot:tgbot /opt/tgbot
```

## 2) Create virtualenv and install deps
```bash
sudo -u tgbot python3 -m venv /opt/tgbot/.venv
sudo -u tgbot /opt/tgbot/.venv/bin/pip install -U pip wheel
sudo -u tgbot /opt/tgbot/.venv/bin/pip install telethon uvloop
```

## 3) Install both services
```bash
sudo cp /opt/tgbot/tgbot-fast.service.example /etc/systemd/system/tgbot-fast.service
sudo cp /opt/tgbot/tgbot-backoffice.service.example /etc/systemd/system/tgbot-backoffice.service
sudo systemctl daemon-reload
sudo systemctl enable --now tgbot-fast tgbot-backoffice
```

## 4) Check status and logs
```bash
systemctl status tgbot-fast --no-pager
systemctl status tgbot-backoffice --no-pager
journalctl -u tgbot-fast -f
journalctl -u tgbot-backoffice -f
```

## 5) First authorization
Because sessions are separate, authenticate both services once:
```bash
cd /opt/tgbot
sudo -u tgbot /opt/tgbot/.venv/bin/python fast_responder.py
sudo -u tgbot /opt/tgbot/.venv/bin/python backoffice.py
```
Stop each process after successful login, then start systemd services again.

## 6) Safe rollout order
1. Stop old monolith service.
2. Start `backoffice.py` manually and authenticate its session.
3. Start `fast_responder.py` manually and authenticate its session.
4. Start `tgbot-fast.service` first.
5. Start `tgbot-backoffice.service` second.
6. Verify:
   - fast responder replies instantly;
   - commands work;
   - verify/delete/first-check still happen after reply.

## 7) Updating code
```bash
sudo rsync -a --delete ./ /opt/tgbot/
sudo chown -R tgbot:tgbot /opt/tgbot
sudo systemctl restart tgbot-fast tgbot-backoffice
```
