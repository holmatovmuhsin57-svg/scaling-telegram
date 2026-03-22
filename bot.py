"""Legacy entrypoint.

Use `fast_responder.py` and `backoffice.py` as separate services.
"""

from tg_bot.fast_responder import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
