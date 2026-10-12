"""LLM tool: hand real work to the backend agent as a tracked, background Claude session."""

import uuid
from pathlib import Path

from loguru import logger

from backend import BackendResult
from pipecat.frames.frames import FunctionCallResultProperties


def make_tools(backend, registry, settings_for, on_finish=None):
    state = {"session": None, "running": False}  # ponytail: one task at a time, single user

    async def delegate(params, task: str):
        """Hand a real task (files, shell, web, code) to the backend agent and return its answer. Use for anything beyond casual conversation. If the answer is a question from Claude, ask the user and call this again with their reply to continue the same session.

        Args:
            task: A complete, self-contained description of what to do.
        """
        if state["running"]:
            await params.result_callback({
                "status": "busy",
                "instruction": "Tell the user you are still working on the previous task."})
            return
        state["running"] = True
        try:
            resume = state["session"] is not None
            sid = state["session"] or str(uuid.uuid4())
            state["session"] = sid
            registry.upsert_session(sid, origin="delegated", mode="headless", state="running")
            task_id = registry.create_task(sid, task)
            # Early non-final result: the call is async, so the user can keep talking while it runs.
            await params.result_callback(
                {"status": "started", "instruction": "Briefly tell the user you've started; "
                 "you will be told when it's done."},
                properties=FunctionCallResultProperties(run_llm=False, is_final=False))
            settings = settings_for(sid)
            try:
                res = await backend.run(task, session_id=sid, resume=resume, settings=settings)
            except Exception as e:  # a background call that never reports back would hang forever
                logger.error(f"backend failed: {e!r}")
                res = BackendResult(text="The task failed to run.", is_error=True)
            finally:
                if settings:  # holds the hook token: do not leave it lying around
                    Path(settings).unlink(missing_ok=True)
                if on_finish:  # a run that ended can no longer use an answer
                    on_finish(sid)
            status = "error" if res.is_error else "done"
            registry.finish_task(task_id, status, res.text[:500])
            registry.set_state(sid, "idle")
            await params.result_callback({"status": status, "result": res.text})
        finally:
            state["running"] = False

    return [delegate]
