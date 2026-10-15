import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from telegram import Telegram


def _bot(status=200):
    seen = []

    async def handler(request):
        if request.content_type == "application/json":
            body = await request.json()
        else:
            body = {k: v if isinstance(v, str) else v.file.read() for k, v in (await request.post()).items()}
        seen.append((request.match_info["token"], request.match_info["method"], body))
        return web.json_response({"ok": status == 200}, status=status)

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", handler)
    return TestServer(app), seen


def _run(server, calls):
    async def go():
        await server.start_server()
        try:
            tg = Telegram("T0K", 42, base=str(server.make_url("")).rstrip("/"))
            for name, *args in calls:
                await getattr(tg, name)(*args)
        finally:
            await server.close()

    asyncio.run(go())


def test_photos_and_messages_reach_the_bot_api_for_the_configured_chat():
    server, seen = _bot()
    _run(server, [("send_photo", b"\x89PNG", "1. 14:02:11 browser_click: Search → 001.png"),
                  ("send_message", "Browser steps\ndone: 1 step")])
    (tok, method, photo), (_, method2, msg) = seen
    assert (tok, method) == ("T0K", "sendPhoto")
    assert photo == {"chat_id": "42", "caption": "1. 14:02:11 browser_click: Search → 001.png", "photo": b"\x89PNG"}
    assert (method2, msg) == ("sendMessage", {"chat_id": "42", "text": "Browser steps\ndone: 1 step"})


def test_a_long_report_and_caption_are_cut_to_the_bot_api_limits():
    server, seen = _bot()
    _run(server, [("send_message", "x" * 5000), ("send_photo", b"p", "y" * 2000)])
    assert len(seen[0][2]["text"]) == 4096 and len(seen[1][2]["caption"]) == 1024


def test_telegram_failures_never_raise():
    server, seen = _bot(status=500)
    _run(server, [("send_message", "hi")])  # HTTP 500: logged, not raised
    asyncio.run(Telegram("T", 1, base="http://127.0.0.1:9").send_message("hi"))  # nothing listening


def test_telegram_is_used_only_when_both_secrets_are_set(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert Telegram.from_env() is None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    assert Telegram.from_env().chat_id == "42"


def test_a_rate_limited_message_is_retried_after_the_wait_telegram_asks_for():
    # Opus review of PR #21: with every tool hooked, a burst of steps can exceed ~1 message/s per chat;
    # a 429 used to be logged and the message (maybe the final report) lost.
    seen, refused = [], set()

    async def handler(request):
        method = request.match_info["method"]
        if method not in refused:  # refuse the first try of each method, as Telegram does in a burst
            refused.add(method)
            return web.json_response({"ok": False, "parameters": {"retry_after": 0.2}}, status=429)
        body = await request.json() if request.content_type == "application/json" else dict(await request.post())
        seen.append((method, asyncio.get_running_loop().time(), body))
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", handler)

    async def go():
        server = TestServer(app)
        await server.start_server()
        try:
            tg = Telegram("T0K", 42, base=str(server.make_url("")).rstrip("/"))
            t0 = asyncio.get_running_loop().time()
            await tg.send_message("Errand steps\ndone: 9 steps")
            await tg.send_photo(b"\x89PNG", "3. browser_click: Buy")
            return t0
        finally:
            await server.close()

    t0 = asyncio.run(go())
    assert [m for m, _, _ in seen] == ["sendMessage", "sendPhoto"]  # both delivered on the retry
    assert seen[0][1] - t0 >= 0.2  # it waited as asked
    assert seen[0][2]["text"] == "Errand steps\ndone: 9 steps" and seen[1][2]["caption"] == "3. browser_click: Buy"


def test_send_message_reports_whether_it_was_delivered():
    async def go(status):
        server, _ = _bot(status=status)
        await server.start_server()
        try:
            return await Telegram("T", 1, base=str(server.make_url("")).rstrip("/")).send_message("hi")
        finally:
            await server.close()

    assert asyncio.run(go(200)) is True and asyncio.run(go(500)) is False
    assert asyncio.run(Telegram("T", 1, base="http://127.0.0.1:9").send_message("hi")) is False
