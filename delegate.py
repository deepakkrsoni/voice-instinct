"""LLM tools: hand real work to the backend agent, with code-enforced spoken confirmation."""

from confirm import classify, last_user_text


def _describe(d: dict) -> str:
    inp = d.get("tool_input") or {}
    detail = inp.get("command") or inp.get("file_path") or inp.get("path") or ""
    return f"{d.get('tool_name', 'a tool')}: {detail}".strip(": ")


def make_tools(backend):
    state = {"session": None, "pending": None}  # ponytail: one task at a time, single user

    async def _run(params, task, extra=()):
        res = await backend.run(task, session_id=state["session"], extra_allowed=extra)
        state["session"] = res.session_id or state["session"]
        if res.denials:
            state["pending"] = (task, res.denials)
            await params.result_callback({
                "status": "needs_confirmation",
                "action": "; ".join(_describe(d) for d in res.denials),
                "instruction": "Tell the user in one sentence what it wants to do and ask "
                               "yes or no. After they answer, call confirm_pending.",
            })
            return
        state["pending"] = None
        await params.result_callback(
            {"status": "error" if res.is_error else "done", "result": res.text}
        )

    async def delegate(params, task: str):
        """Hand a real task (files, shell, web, code) to the backend agent and return its answer. Use for anything beyond casual conversation.

        Args:
            task: A complete, self-contained description of what to do.
        """
        await _run(params, task)

    async def confirm_pending(params):
        """Run the action the user was just asked to approve. Call only after the user has answered the confirmation question."""
        if not state["pending"]:
            await params.result_callback({"status": "nothing_pending"})
            return
        verdict = classify(last_user_text(params.context.get_messages()))
        if verdict == "unclear":
            await params.result_callback(
                {"status": "unclear", "instruction": "Ask the user to answer yes or no."}
            )
            return
        task, denials = state["pending"]
        state["pending"] = None
        if verdict == "no":
            await params.result_callback({"status": "cancelled"})
            return
        extra = sorted({d["tool_name"] for d in denials})  # ponytail: allows whole tool, not just that command
        await _run(params, f"The user approved. Retry: {task}", extra)

    return [delegate, confirm_pending]
