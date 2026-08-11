"""Checks for the docker.log cleaner: `pytest bench/test_clean_log.py`."""

import clean_log

RAW = """junk before the first timestamp
more startup noise
2026-08-11 11:28:51 | INFO     | policy | "Configuring security policy"
2026-08-11 11:28:56 | INFO     | loop | (---------iteration 1)
2026-08-11 11:30:38 | INFO     | loop | (RESPONSE: (RESULTS: ((COMMAND_RETURN: ((@Agent-A _quote_Do the thing_quote_)) [_apostrophe_@Agent-A_apostrophe_, _apostrophe_Do the thing_apostrophe_])))))
2026-08-11 11:31:09 | INFO     | loop | (CHARS_SENT: 12076 PROMPT: the whole rebuilt prompt goes here)
2026-08-11 11:31:15 | INFO     | openai | [LLM_USAGE] provider=OpenAI model=gpt-5.6-luna input_tokens=10 output_tokens=5 total_tokens=15 cached_tokens=0
2026/08/11 11:43:47 [warn] 20#20: nginx noise, no timestamp prefix
"""


def test_startup_noise_is_collapsed_to_one_line():
    out = clean_log.clean(RAW)
    assert "[omitted 2 lines of MeTTa interpreter startup]" in out
    assert "junk before" not in out


def test_unwraps_the_command_return_and_unescapes_it():
    out = clean_log.clean(RAW)
    assert '(@Agent-A "Do the thing")' in out
    assert "_quote_" not in out and "_apostrophe_" not in out


def test_drops_the_rebuilt_prompt_and_nginx_noise():
    out = clean_log.clean(RAW)
    assert "the whole rebuilt prompt" not in out
    assert "nginx noise" not in out


def test_keeps_llm_usage_and_iteration_markers():
    out = clean_log.clean(RAW)
    assert "iteration 1" in out
    assert "LLM_USAGE" in out


def test_unwrap_falls_back_to_the_unescaped_line_when_shape_is_unrecognized():
    message = "(RESPONSE: (@Agent-A _quote_hi_quote_))"
    assert clean_log.unwrap_response(clean_log.unescape(message)) == '(@Agent-A "hi")'


def test_feeding_lines_one_at_a_time_matches_cleaning_the_whole_file_at_once():
    cleaner = clean_log.Cleaner()
    streamed = [cleaner.feed(line) for line in RAW.splitlines()]
    streamed_text = "\n".join(c for c in streamed if c is not None) + "\n"

    assert streamed_text == clean_log.clean(RAW)


def test_follow_prints_each_cleaned_line_as_it_arrives(capsys):
    clean_log.follow(iter(RAW.splitlines(keepends=True)))
    out = capsys.readouterr().out

    assert "[omitted 2 lines of MeTTa interpreter startup]" in out
    assert '(@Agent-A "Do the thing")' in out
    assert "_quote_" not in out
