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
