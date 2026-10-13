import asyncio
import signal

from shutdown import cancel_on_exit


class FakeRunner:
    cancels = 0

    async def cancel(self):
        self.cancels += 1


def test_sigterm_cancels_open_sessions_and_still_reaches_the_server():
    seen = []
    original = signal.signal(signal.SIGTERM, lambda sig, frame: seen.append(sig))  # stands in for uvicorn
    try:
        open_runner, closed_runner = FakeRunner(), FakeRunner()
        sessions = {open_runner}

        async def main():
            cancel_on_exit(sessions)
            cancel_on_exit(sessions)  # every session installs it; the server must still hear the signal once
            signal.raise_signal(signal.SIGTERM)
            await asyncio.sleep(0.05)

        asyncio.run(main())
        assert (open_runner.cancels, closed_runner.cancels) == (1, 0)
        assert seen == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, original)
