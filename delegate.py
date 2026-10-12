"""LLM tool: hand real work to the backend agent as a tracked, background Claude session."""

import uuid
from pathlib import Path

from loguru import logger

from backend import BackendResult
from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams
from workspace import resolve_workdir


def make_tools(backend, registry, settings_for, on_finish=None, workspace_root=None, home=None):
    home = Path(home) if home else Path.home()
    workspace_root = Path(workspace_root) if workspace_root else home / ".voice-instinct" / "workspace"
    state = {"session": None, "cwd": None, "running": False}  # ponytail: one task at a time, single user

    # Async call (Pipecat: Function Calling > Per-Tool Options): barge-in must not cancel it, and the
    # framework deadline must outlive the backend's own timeout.
    @tool_options(cancel_on_interruption=False, timeout_secs=getattr(backend, "timeout", 300) + 30)
    async def delegate(params: FunctionCallParams, task: str, directory: str = ""):
        """Hand a real task (files, shell, web, code) to the backend agent and return its answer. Use for anything beyond casual conversation. If the answer is a question from Claude, ask the user and call this again with their reply to continue the same session.

        Args:
            task: A complete, self-contained description of what to do.
            directory: The folder to work in, only if the user named one (for example ~/code/snake). Leave empty to use the private default workspace or to continue the current session.
        """
        if state["running"]:
            await params.result_callback({
                "status": "busy",
                "instruction": "Tell the user you are still working on the previous task."})
            return
        state["running"] = True
        try:
            new_sid = str(uuid.uuid4())
            try:
                if directory and directory.strip():
                    wd = resolve_workdir(directory, task, new_sid, workspace_root, home)
                    resume = state["session"] is not None and state["cwd"] == str(wd)
                    sid = state["session"] if resume else new_sid
                elif state["session"]:
                    resume, sid, wd = True, state["session"], Path(state["cwd"])
                else:
                    resume, sid = False, new_sid
                    wd = resolve_workdir("", task, sid, workspace_root, home)
            except (ValueError, OSError) as e:
                await params.result_callback({"status": "error", "result": str(e)})
                return
            state["session"], state["cwd"] = sid, str(wd)
            registry.upsert_session(sid, origin="delegated", mode="headless", state="running", cwd=str(wd))
            task_id = registry.create_task(sid, task)
            # Intermediate result (documented: is_final=False). run_llm=True on it is not in the docs
            # (source honours it): without it Gemini calls the tool and says nothing, so the user hears silence.
            await params.result_callback(
                {"status": "started", "instruction": "In one short sentence tell the user you've "
                 "started and where; you will be told when it's done."},
                properties=FunctionCallResultProperties(run_llm=True, is_final=False))
            settings = settings_for(sid)
            try:
                res = await backend.run(task, session_id=sid, resume=resume, settings=settings, cwd=str(wd))
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
