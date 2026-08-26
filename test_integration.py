from pathlib import Path
from converter_core import generate_ofx, parse_pdf

pdf = Path(__file__).with_name('sample_extrato.pdf')
document = parse_pdf(pdf)
assert len(document.transactions) == 3, document.transactions
assert document.detected_bank == 'Banco do Brasil', document.detected_bank
output = pdf.with_name('sample_extrato.ofx')
output.write_text(generate_ofx(document, document.detected_bank, 'CHECKING', '001', '1234', '5678-9'), encoding='utf-8')
print(f'transacoes={len(document.transactions)} banco={document.detected_bank} saida={output}')
