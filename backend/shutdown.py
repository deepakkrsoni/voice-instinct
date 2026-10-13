"""Stop open sessions when the server is told to stop.

The development runner leaves signals to uvicorn, and uvicorn waits for each session to finish
before it shuts the WebRTC connections that would end it, so a stop with a session open hangs
until systemd kills it. Pipecat documents no fix for server mode; this chains onto uvicorn's
handler and cancels the sessions with the documented `WorkerRunner.cancel()`.
"""

import asyncio
import signal


def cancel_on_exit(sessions: set, signals=(signal.SIGINT, signal.SIGTERM)) -> None:
    """Call from inside a session (after uvicorn installed its handlers). Safe to call again."""
    loop = asyncio.get_running_loop()
    for sig in signals:
        previous = signal.getsignal(sig)
        if getattr(previous, "cancels_sessions", False):
            continue

        def handler(signum, frame, previous=previous):
            for runner in list(sessions):
                loop.call_soon_threadsafe(loop.create_task, runner.cancel())
            if callable(previous):
                previous(signum, frame)

        handler.cancels_sessions = True
        signal.signal(sig, handler)
