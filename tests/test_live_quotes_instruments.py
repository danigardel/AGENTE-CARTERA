import unittest

from scripts.generate_live_quotes_instruments import OUTPUT, render_instruments_module


class LiveQuotesInstrumentMapTests(unittest.TestCase):
    def test_worker_allowlist_matches_configured_ls_instruments(self):
        self.assertEqual(
            OUTPUT.read_text(encoding="utf-8"),
            render_instruments_module(),
        )


if __name__ == "__main__":
    unittest.main()
