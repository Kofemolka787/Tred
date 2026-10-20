"""Interactive setup failures seen by the owner; no external credentials/API."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from configure_ai import main, read_key, read_model, save, verify_key
from trading_core import Ledger

KEY = "fixture-visible-input-not-a-real-key"


class ConfigureAITests(unittest.TestCase):
    def test_blank_key_repeats_key_prompt_instead_of_advancing_to_model(self):
        prompts = []
        answers = iter(("", KEY))
        def reader(prompt):
            prompts.append(prompt)
            return next(answers)
        out = io.StringIO()
        with patch('builtins.input', side_effect=reader), redirect_stdout(out):
            self.assertEqual(read_key(), KEY)
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all('видимый ввод' in p for p in prompts))
        self.assertIn('Ключ не введён', out.getvalue())
        self.assertIn(str(len(KEY)), out.getvalue())
        self.assertNotIn(KEY, out.getvalue())  # Only terminal echo shows input.

    def test_key_pasted_into_model_is_reprompted_and_enter_chooses_default(self):
        out = io.StringIO()
        with patch('builtins.input', side_effect=['sk-fixture-not-a-real-key', '']), redirect_stdout(out):
            self.assertEqual(read_model(), 'gpt-5-mini')
        self.assertIn('ключ здесь не нужен', out.getvalue())
        self.assertNotIn('sk-fixture', out.getvalue())

    def test_visible_setup_saves_verified_key_for_existing_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp)/'ledger.sqlite3')
            ledger.claim(ledger.pair(101)['code'])
            out = io.StringIO()
            with patch('sys.argv', ['configure_ai.py', '--data-dir', tmp]), patch('builtins.input', side_effect=[KEY, '']), patch('configure_ai.verify_key') as verify, redirect_stdout(out):
                main()
            verify.assert_called_once_with(KEY, 'gpt-5-mini')
            settings = json.loads((Path(tmp)/'ai-settings.json').read_text())
            self.assertEqual(settings['api_key'], KEY)
            self.assertEqual(settings['allowed_users'], [101])
            self.assertIn('Ключ сохранён', out.getvalue())
            self.assertNotIn(KEY, out.getvalue())

    def test_provider_failure_preserves_previous_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp)/'ledger.sqlite3')
            ledger.claim(ledger.pair(101)['code'])
            save(tmp, KEY, allowed_users=[101])
            before = (Path(tmp)/'ai-settings.json').read_bytes()
            with patch('sys.argv', ['configure_ai.py', '--data-dir', tmp]), patch('builtins.input', side_effect=['fixture-new-key-not-valid-at-provider', '']), patch('configure_ai.verify_key', side_effect=ValueError('HTTP 401')), redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                main()
            self.assertEqual((Path(tmp)/'ai-settings.json').read_bytes(), before)

    def test_http_errors_distinguish_key_permissions_model_and_quota_without_provider_body(self):
        for status in (401, 403, 404, 429, 503):
            with self.subTest(status=status):
                error = HTTPError('https://api.openai.com/v1/models/gpt-5-mini', status, KEY, {}, io.BytesIO(KEY.encode()))
                with patch('configure_ai.urlopen', side_effect=error), self.assertRaises(ValueError) as caught:
                    verify_key(KEY, 'gpt-5-mini')
                self.assertIn(f'HTTP {status}', str(caught.exception))
                self.assertNotIn(KEY, str(caught.exception))


if __name__ == '__main__': unittest.main()
