"""Run offline with unittest or pytest; speech and uploads are mocked."""
import json
import sys
import types
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN.parents[1] / "src"))
import helper
import media_handler as mh
from text_splitter import split_for_telegram


class SpeakTextTests(unittest.TestCase):
    def setUp(self):
        # Isolate per-piece validation tests from sentence packing.
        self.enterContext(patch.object(mh, "speech_chunks", side_effect=lambda parts: [[p] for p in parts]))
        config = types.ModuleType("config")
        config.config_get_by_key = lambda key, default=None: default
        self.enterContext(patch.dict(sys.modules, {"config": config}))
        self.enterContext(patch.object(mh, "_tts_allowed", return_value=True))
        self.enterContext(patch.object(mh, "_prompt_is_unsafe", return_value=False))
        self.enterContext(patch.object(mh, "_live_send_chat_action", None))
        # These tests isolate text cleanup/chunking from language routing.
        self.enterContext(patch.object(mh, "speech_parts", side_effect=lambda text, voice:
                                      [(piece, voice) for piece in split_for_telegram(text)]))
        self.synth = self.enterContext(patch.object(
            mh, "_synthesise_speech", side_effect=lambda text, voice: text.encode()))
        self.send = self.enterContext(patch.object(mh, "_live_send_voice"))

    def delivered(self):
        return [call.args[0].decode() for call in self.send.call_args_list]

    def test_boundary_and_long_text(self):
        for size in (4096, 4097, 12013):
            with self.subTest(size=size):
                self.send.reset_mock()
                text = "я" * size
                self.assertEqual(mh.speak(text), "VOICE_SENT")
                parts = self.delivered()
                self.assertEqual("".join(parts), text)
                self.assertTrue(all(0 < len(part) <= 4096 for part in parts))
                self.assertEqual(len(parts), (size + 4095) // 4096)

    def test_actual_and_escaped_newlines(self):
        expected = "First\nSecond\n\nsend this as speech"
        for text in (expected, expected.replace("\n", "\\n")):
            self.send.reset_mock()
            self.assertEqual(mh.speak(text), "VOICE_SENT")
            self.assertEqual(self.delivered(), [expected])

    def test_long_paragraphs_use_send_splitting(self):
        text = "a" * 3000 + "\n\n" + "b" * 3000 + "\n" + "c" * 2000
        self.assertEqual(mh.speak(text), "VOICE_SENT")
        self.assertEqual(self.delivered(), split_for_telegram(text))
        self.assertEqual(self.delivered(), ["a" * 3000, "b" * 3000, "c" * 2000])

    def test_parser_to_speech(self):
        for text in ("First\nSecond", "First\\nsend this aloud"):
            with self.subTest(text=text), patch.object(
                    helper, "LLM_COMMANDS", helper.LLM_COMMANDS | {"speak"}):
                self.send.reset_mock()
                parsed = helper.balance_parentheses("speak " + text)
                argument = json.loads(parsed[len("((speak "):-2])
                self.assertEqual(mh.speak(argument), "VOICE_SENT")
                self.assertEqual(self.delivered(), [text.replace("\\n", "\n")])

    def test_synthesis_failure_continues(self):
        self.synth.side_effect = [b"first", None, None, b"third"]
        result = mh.speak("x" * 9000)
        self.assertIn("part 2/3", result)
        self.assertIn("2 parts already sent", result)
        self.assertEqual(self.delivered(), ["first", "third"])
        self.assertEqual(self.synth.call_count, 4)

    def test_send_failure_continues(self):
        self.send.side_effect = [None, RuntimeError("upload failed"), None]
        result = mh.speak("x" * 9000)
        self.assertIn("part 2/3: delivery uncertain", result)
        self.assertEqual(self.synth.call_count, 3)
        self.assertEqual(self.send.call_count, 3)

    def test_empty_normalized_input(self):
        self.assertTrue(mh.speak("\\n\\n").startswith("VOICE_INVALID_INPUT"))
        self.synth.assert_not_called()
        self.send.assert_not_called()

    def test_send_rendering_budget(self):
        parts = split_for_telegram("x" * 6000, lambda text: len(text) * 2 <= 4096)
        self.assertEqual("".join(parts), "x" * 6000)
        self.assertTrue(all(len(part) * 2 <= 4096 for part in parts))

    def test_indicator_refreshes_during_synthesis_and_upload_then_stops(self):
        refreshed = threading.Event()
        actions = []

        def action(value):
            actions.append(value)
            if len(actions) >= 2:
                refreshed.set()

        def synth(text, voice):
            self.assertTrue(refreshed.wait(1), "indicator did not refresh during synthesis")
            return b"audio"

        def upload(audio):
            refreshed.clear()
            self.assertTrue(refreshed.wait(1), "indicator did not refresh during upload")

        self.synth.side_effect = synth
        self.send.side_effect = upload
        with patch.object(mh, "_live_send_chat_action", action), patch.object(mh, "RECORDING_REFRESH_SECONDS", .01):
            self.assertEqual(mh.speak("hello"), "VOICE_SENT")
            refreshed.clear()
            self.assertFalse(refreshed.wait(.05), "indicator continued after completion")
        self.assertTrue(all(value == "record_voice" for value in actions))

    def test_indicator_stops_on_failure(self):
        for fail_synthesis in (True, False):
            with self.subTest(synthesis=fail_synthesis):
                action = threading.Event()
                self.synth.side_effect = lambda *args: None if fail_synthesis else b"audio"
                self.send.side_effect = RuntimeError("upload failed")
                with patch.object(mh, "_live_send_chat_action", lambda value: action.set()), patch.object(mh, "RECORDING_REFRESH_SECONDS", .01):
                    self.assertTrue(mh.speak("hello").startswith("VOICE_FAILED"))
                    action.clear()
                    self.assertFalse(action.wait(.05))

    def test_indicator_failure_does_not_block_audio(self):
        with patch.object(mh, "_live_send_chat_action", side_effect=RuntimeError("offline")):
            self.assertEqual(mh.speak("hello"), "VOICE_SENT")
        self.send.assert_called_once()

    def test_cleanup_before_synthesis(self):
        self.assertEqual(mh.speak("## Hello **world** 😀\nRead [the guide](https://example.com/a)."), "VOICE_SENT")
        self.assertEqual(self.delivered(), ["Hello world\nRead the guide."])

    def test_empty_and_removed_content_never_send(self):
        for text in (None, [], "", " ", "😀", "***", "https://example.com", "[https://example.com](https://example.com)"):
            with self.subTest(text=text):
                self.assertTrue(mh.speak(text).startswith("VOICE_INVALID_INPUT"))
        self.assertTrue(mh.speak().startswith("VOICE_INVALID_INPUT"))
        self.synth.assert_not_called()
        self.send.assert_not_called()

    def test_missing_argument_parses_to_validation(self):
        with patch.object(helper, "LLM_COMMANDS", helper.LLM_COMMANDS | {"speak"}):
            for command in ("speak", "(speak)", 'speak ""'):
                self.assertEqual(helper.balance_parentheses(command), '((speak ""))')

    def test_cleanup_preserves_language_and_numbers(self):
        self.assertEqual(mh.speak("Привет **мир**! 123 _слова_ `code` 👩‍💻"), "VOICE_SENT")
        self.assertEqual(self.delivered(), ["Привет мир! 123 слова code"])


if __name__ == "__main__":
    unittest.main()
