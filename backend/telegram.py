"""Debug mode's live view: browser steps to the user's own Telegram chat, over the plain Bot API.

Debug only, one chat. #5 turns this into a Notifier adapter. Failures are logged and swallowed:
the local record in the errand folder is the source of truth.
"""

import os

import aiohttp
from loguru import logger

CAPTION_MAX, MESSAGE_MAX = 1024, 4096  # Bot API limits


class Telegram:
    def __init__(self, token, chat_id, base="https://api.telegram.org"):
        self._url, self.chat_id = f"{base}/bot{token}", str(chat_id)

    @classmethod
    def from_env(cls):
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        return cls(token, chat) if token and chat else None

    async def send_photo(self, png, caption):
        form = aiohttp.FormData()
        form.add_field("chat_id", self.chat_id)
        form.add_field("caption", caption[:CAPTION_MAX])
        form.add_field("photo", png, filename="step.png", content_type="image/png")
        await self._post("sendPhoto", data=form)

    async def send_message(self, text):
        # ponytail: a report past 4096 chars is cut; send steps.md as a document if errands outgrow it
        await self._post("sendMessage", json={"chat_id": self.chat_id, "text": text[:MESSAGE_MAX]})

    async def _post(self, method, **body):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with s.post(f"{self._url}/{method}", **body) as r:
                    if r.status != 200:
                        logger.warning(f"telegram {method} failed: HTTP {r.status}")
        except Exception as e:  # the type only: the error text can carry the URL, which holds the token
            logger.warning(f"telegram {method} failed: {type(e).__name__}")
