import unittest
from pathlib import Path

from converter_core import inspect_pdf_layout, parse_pdf


class LayoutAlertTests(unittest.TestCase):
    def test_overlap_is_reported(self):
        path = Path(__file__).with_name('overlap_extrato.pdf')
        warnings = inspect_pdf_layout(path)
        self.assertTrue(any('SOBREPOSIÇÃO' in warning for warning in warnings), warnings)
        document = parse_pdf(path)
        self.assertTrue(document.layout_warnings)


if __name__ == '__main__':
    unittest.main()
