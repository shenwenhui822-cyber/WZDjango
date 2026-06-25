from django.test import SimpleTestCase
import pandas as pd

from position_daily.services.stock_contribution import (
    calc_row_daily_pnl,
    enrich_positions,
)


SAMPLE_ROW = {
    "code": "301558.SZ",
    "name": "三态股份",
    "volume": 400,
    "available_volume": 0,
    "cost_price": 6.3,
    "market_value": 2512,
    "profit": -8.8,
    "last_price": 6.28,
    "prev_close": 6.42,
    "change_amount": -0.14,
    "change_pct": -2.18,
    "weight": 0.001,
}


class DailyPnlTests(SimpleTestCase):
    def test_hold_position_uses_volume_times_change_amount(self):
        pnl = calc_row_daily_pnl(SAMPLE_ROW, prev_volume=None)
        self.assertAlmostEqual(pnl, -56.0)

    def test_old_formula_would_differ(self):
        old = SAMPLE_ROW["market_value"] * SAMPLE_ROW["change_pct"] / 100
        new = calc_row_daily_pnl(SAMPLE_ROW, prev_volume=400)
        self.assertAlmostEqual(new, -56.0)
        self.assertNotAlmostEqual(old, new, places=1)

    def test_new_buy_uses_cost_to_last(self):
        pnl = calc_row_daily_pnl(SAMPLE_ROW, prev_volume=0)
        self.assertAlmostEqual(pnl, 400 * (6.28 - 6.3))

    def test_buy_add_mixed(self):
        pnl = calc_row_daily_pnl(SAMPLE_ROW, prev_volume=200)
        expected = 200 * -0.14 + 200 * (6.28 - 6.3)
        self.assertAlmostEqual(pnl, expected)

    def test_partial_sell_includes_sold_portion(self):
        pnl = calc_row_daily_pnl(SAMPLE_ROW, prev_volume=600)
        expected = 400 * -0.14 + 200 * -0.14
        self.assertAlmostEqual(pnl, expected)

    def test_enrich_positions_adds_columns(self):
        df = pd.DataFrame([SAMPLE_ROW])
        prev = pd.DataFrame([{**SAMPLE_ROW, "volume": 400}])
        out = enrich_positions(df, prev)
        self.assertIn("daily_contrib", out.columns)
        self.assertAlmostEqual(float(out.iloc[0]["daily_contrib"]), -56.0)
        self.assertEqual(out.iloc[0]["trade_note"], "hold")
