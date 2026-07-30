import unittest

from figures import choose_significant_figure, extract_figures


class FigureParsingTests(unittest.TestCase):
    def test_money_percent_and_year(self):
        text = (
            "In twenty twenty five, it rose from forty six per cent "
            "to two hundred and sixty four million pounds."
        )
        figures = extract_figures(text)
        self.assertEqual(
            [(f.value, f.unit, f.display) for f in figures],
            [
                (2025.0, "year", "2025"),
                (46.0, "percent", "46%"),
                (264_000_000.0, "GBP", "£264,000,000"),
            ],
        )

    def test_decimal_and_fractional_scale(self):
        text = "Seven point nine million pounds and four and a half thousand shops."
        figures = extract_figures(text)
        self.assertEqual(figures[0].display, "£7,900,000")
        self.assertEqual(figures[1].value, 4_500)
        self.assertEqual(figures[1].unit, "count:shop")

    def test_exact_offsets_round_trip(self):
        text = "The loss was nine hundred pounds, not ten pounds."
        for figure in extract_figures(text):
            self.assertEqual(text[figure.start:figure.end], figure.raw)

    def test_significance_prefers_striking_money(self):
        text = "Two tourists paid nine hundred pounds for two packets."
        self.assertEqual(choose_significant_figure(text).display, "£900")

    def test_ordinary_twenty_eight_is_not_a_year(self):
        figure = extract_figures("Twenty eight freeholders were warned.")[0]
        self.assertEqual(figure.value, 28)
        self.assertFalse(figure.is_year)

    def test_punctuation_separates_adjacent_figures(self):
        figures = extract_figures("In twenty twenty five, two tourists paid.")
        self.assertEqual([f.value for f in figures], [2025, 2])

    def test_two_thousand_items_is_not_assumed_to_be_a_year(self):
        figure = extract_figures("More than two thousand counterfeit bars.")[0]
        self.assertEqual(figure.value, 2000)
        self.assertFalse(figure.is_year)


if __name__ == "__main__":
    unittest.main()
