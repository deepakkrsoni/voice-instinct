from confirm import classify, last_user_text


def test_yes():
    for s in ["yes", "Yeah, go ahead.", "sure", "ok do it"]:
        assert classify(s) == "yes", s


def test_no_wins_over_yes():
    for s in ["no", "No, don't do it", "yeah no", "ok but don't", "cancel that"]:
        assert classify(s) == "no", s


def test_unclear():
    for s in ["hmm maybe", "what would it do?", ""]:
        assert classify(s) == "unclear", s


def test_last_user_text_handles_str_and_parts():
    msgs = [
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": "Allow it?"},
        {"role": "user", "content": [{"type": "text", "text": "not sure"}]},
    ]
    assert last_user_text(msgs) == "not sure"
    assert last_user_text([]) == ""


def test_user_turns_counts_only_user_messages():
    from confirm import user_turns

    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
            {"role": "developer", "content": "c"}, {"role": "user", "content": "d"}]
    assert user_turns(msgs) == 2 and user_turns([]) == 0


def test_approving_a_cancellation_or_a_stop_is_a_yes():
    # Cancelling subscriptions is a core errand: "yes, cancel it" approves it.
    for s in ["Yes, cancel it.", "Yeah go ahead and cancel", "Yes, stop the subscription", "sure, cancel my plan"]:
        assert classify(s) == "yes", s


def test_a_question_or_a_hesitation_is_never_a_yes():
    # A clarifying question must not approve; asking again is the safe outcome.
    for s in ["Okay, what would that do?", "ok hold on", "okay... let me think", "yes? which one", "sure, wait"]:
        assert classify(s) == "unclear", s


def test_a_real_refusal_still_wins():
    for s in ["yes no", "yeah, don't", "ok never mind", "no, cancel it", "nope, stop"]:
        assert classify(s) == "no", s
