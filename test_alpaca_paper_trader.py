import unittest
from unittest.mock import patch

from alpaca_paper_trader import AlpacaPaperTrader, PaperTradeError


class PaperBrokerResponseValidationTests(unittest.TestCase):
    def setUp(self):
        self.trader = AlpacaPaperTrader("paper-key", "paper-secret")

    def test_account_and_clock_reject_non_objects(self):
        for method_name in ("account", "clock"):
            with self.subTest(method=method_name), patch.object(self.trader, "_request", return_value=[]):
                with self.assertRaises(PaperTradeError):
                    getattr(self.trader, method_name)()

    def test_positions_and_orders_reject_non_lists_and_bad_records(self):
        for method_name in ("positions", "orders"):
            for response in ({}, ["not-an-object"]):
                with self.subTest(method=method_name, response=response), patch.object(
                    self.trader, "_request", return_value=response
                ):
                    with self.assertRaises(PaperTradeError):
                        getattr(self.trader, method_name)()


if __name__ == "__main__":
    unittest.main()
