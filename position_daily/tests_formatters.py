from django.test import SimpleTestCase

from position_daily.services.formatters import format_money


class FormatMoneyTests(SimpleTestCase):
    def test_with_decimals(self):
        self.assertEqual(format_money(9795273.39), "9,795,273.39")

    def test_integer_style(self):
        self.assertEqual(format_money(25460125, digits=0), "25,460,125")

    def test_negative(self):
        self.assertEqual(format_money(-14643.58), "-14,643.58")

    def test_none(self):
        self.assertEqual(format_money(None), "—")
