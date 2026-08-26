import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from converter_core import ParsedDocument, Transaction, detect_bank, generate_ofx, parse_money, parse_transactions


class ConverterCoreTests(unittest.TestCase):
    def test_parse_brazilian_money(self):
        self.assertEqual(parse_money("1.234,56"), Decimal("1234.56"))
        self.assertEqual(parse_money("R$ 89,90 D"), Decimal("-89.90"))
        self.assertEqual(parse_money("(12,50)"), Decimal("-12.50"))
        self.assertEqual(parse_money("100.00"), Decimal("100.00"))

    def test_parse_transactions_and_signs(self):
        text = """
        BANCO DO BRASIL
        Data Histórico Valor
        02/01/2025 PIX RECEBIDO CLIENTE 1.250,00 C
        03/01/2025 PAGAMENTO FORNECEDOR 230,40 D
        Saldo final 10.000,00
        """
        transactions, warnings = parse_transactions(text)
        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0].date, date(2025, 1, 2))
        self.assertEqual(transactions[0].amount, Decimal("1250.00"))
        self.assertEqual(transactions[1].amount, Decimal("-230.40"))
        self.assertFalse(warnings)

    def test_bank_detection(self):
        self.assertEqual(detect_bank("Extrato BANCO DO BRASIL agência 123"), "Banco do Brasil")
        self.assertEqual(detect_bank("documento sem identificação"), "Detecção automática")

    def test_ofx_contains_required_transaction_fields(self):
        document = ParsedDocument(
            source_path=Path("extrato_teste.pdf"),
            text="",
            transactions=[Transaction(date(2025, 1, 2), "PIX RECEBIDO", Decimal("1250.00"))],
            detected_bank="Banco do Brasil",
        )
        ofx = generate_ofx(document, "Banco do Brasil", "CHECKING", "001", "123", "4567-8")
        self.assertIn("OFXHEADER:100", ofx)
        self.assertIn("<TRNTYPE>CREDIT", ofx)
        self.assertIn("<DTPOSTED>20250102000000[-3:BRT]", ofx)
        self.assertIn("<TRNAMT>1250.00", ofx)
        self.assertIn("<FITID>", ofx)
        self.assertIn("<ACCTID>4567-8", ofx)


if __name__ == "__main__":
    unittest.main()
