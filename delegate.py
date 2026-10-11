"""LLM tools: hand real work to the backend agent, with code-enforced spoken confirmation."""

from confirm import classify, last_user_text


def _describe(d: dict) -> str:
    inp = d.get("tool_input") or {}
    detail = inp.get("command") or inp.get("file_path") or inp.get("path") or ""
    return f"{d.get('tool_name', 'a tool')}: {detail}".strip(": ")


def _user_turns(params) -> int:
    return sum(1 for m in params.context.get_messages() if m.get("role") == "user")


def _allow_rules(denials: list[dict]) -> list[str]:
    """Narrowest rule per denial: the exact Bash command if known, else the whole tool."""
    rules = set()
    for d in denials:
        name, cmd = d.get("tool_name"), (d.get("tool_input") or {}).get("command")
        if name == "Bash" and cmd:
            rules.add(f"Bash({cmd})")
        elif name:
            rules.add(name)
    return sorted(rules)


def make_tools(backend):
    state = {"session": None, "pending": None}  # ponytail: one task at a time, single user

    async def _run(params, task, extra=()):
        res = await backend.run(task, session_id=state["session"], extra_allowed=extra)
        state["session"] = res.session_id or state["session"]
        if res.denials:
            state["pending"] = (task, res.denials, _user_turns(params))
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
        if _user_turns(params) <= state["pending"][2]:
            # The user has not spoken since the question: words in the original request are not an answer.
            await params.result_callback(
                {"status": "unclear", "instruction": "Ask the user yes or no first."}
            )
            return
        verdict = classify(last_user_text(params.context.get_messages()))
        if verdict == "unclear":
            await params.result_callback(
                {"status": "unclear", "instruction": "Ask the user to answer yes or no."}
            )
            return
        task, denials, _ = state["pending"]
        state["pending"] = None
        if verdict == "no":
            await params.result_callback({"status": "cancelled"})
            return
        approved = "; ".join(_describe(d) for d in denials)
        await _run(params, f"The user approved exactly: {approved}. Retry: {task}", _allow_rules(denials))

    return [delegate, confirm_pending]
