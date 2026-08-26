from __future__ import annotations

import hashlib
import re
import uuid
from itertools import combinations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from pathlib import Path
from typing import Iterable, Optional


BANKS = {
    "Detecção automática": {"code": "00000000", "patterns": ()},
    "Banco do Brasil": {"code": "001", "patterns": ("BANCO DO BRASIL", "BB.COM.BR")},
    "Bradesco": {"code": "237", "patterns": ("BRADESCO", "BANCO BRADESCO")},
    "Itaú": {"code": "341", "patterns": ("ITAÚ", "ITAU", "BANCO ITAU")},
    "Santander": {"code": "033", "patterns": ("SANTANDER",)},
    "Caixa Econômica Federal": {"code": "104", "patterns": ("CAIXA ECONÔMICA", "CAIXA ECONOMICA", "CEF")},
    "Nubank": {"code": "260", "patterns": ("NUBANK", "NU PAGAMENTOS")},
    "Banco Inter": {"code": "077", "patterns": ("BANCO INTER", "BANCOINTER", "INTER S.A.")},
    "Sicredi": {"code": "748", "patterns": ("SICREDI",)},
    "Sicoob": {"code": "756", "patterns": ("SICOOB",)},
    "Safra": {"code": "422", "patterns": ("SAFRA",)},
    "BTG Pactual": {"code": "208", "patterns": ("BTG PACTUAL", "BTG")},
    "Mercado Pago": {"code": "323", "patterns": ("MERCADO PAGO",)},
}

DATE_RE = re.compile(r"(?<!\d)(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})(?!\d)")
NUMBER_RE = re.compile(
    r"(?<![\w])(?:R\$\s*)?[-+]?\(?\s*\d{1,3}(?:[.\s]\d{3})*(?:,\d{2,4})?\s*\)?(?:\s*[DC])?(?![\w])"
    r"|(?<![\w])(?:R\$\s*)?[-+]?\(?\s*\d+[.,]\d{2,4}\s*\)?(?:\s*[DC])?(?![\w])"
)

EXCLUDED_LINE_RE = re.compile(
    r"\b(saldo\s+(anterior|final|disponível|disponivel)|total|lançamento|lancamento|período|periodo|data de emissão|data de emissao)\b",
    re.IGNORECASE,
)


@dataclass
class Transaction:
    date: date
    memo: str
    amount: Decimal
    source_line: str = ""
    confidence: str = "alta"

    @property
    def kind(self) -> str:
        return "Crédito" if self.amount >= 0 else "Débito"


@dataclass
class ParsedDocument:
    source_path: Path
    text: str
    transactions: list[Transaction] = field(default_factory=list)
    detected_bank: str = "Detecção automática"
    warnings: list[str] = field(default_factory=list)
    layout_warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.source_path.name

    @property
    def date_range(self) -> tuple[Optional[date], Optional[date]]:
        if not self.transactions:
            return None, None
        dates = [item.date for item in self.transactions]
        return min(dates), max(dates)


def read_pdf_text(path: Path) -> str:
    """Extrai texto de PDFs digitais localmente, sem enviar o arquivo para a internet."""
    errors: list[str] = []
    try:
        import pdfplumber  # type: ignore

        pages: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
        text = "\n".join(pages).strip()
        if text:
            return text
    except Exception as exc:  # pragma: no cover - depende do PDF e do ambiente
        errors.append(str(exc))

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        if text:
            return text
    except Exception as exc:  # pragma: no cover - depende do PDF e do ambiente
        errors.append(str(exc))

    # OCR opcional: se os módulos e o executável Tesseract existirem no Windows,
    # PDFs digitalizados também podem ser processados sem sair do computador.
    try:
        import fitz  # type: ignore
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore

        ocr_pages: list[str] = []
        with fitz.open(str(path)) as pdf:
            for page in pdf:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
                ocr_pages.append(pytesseract.image_to_string(image, lang="por+eng"))
        ocr_text = "\\n".join(ocr_pages).strip()
        if ocr_text:
            return ocr_text
    except Exception as exc:  # pragma: no cover - depende do OCR instalado
        errors.append(f"OCR indisponível: {exc}")

    detail = "; ".join(errors[-2:])
    if detail:
        raise ValueError(f"Não foi possível ler o PDF. Detalhes técnicos: {detail}")
    raise ValueError("Não foi possível extrair texto deste PDF.")


def _intersection_area(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]) -> float:
    left = max(box_a[0], box_b[0])
    top = max(box_a[1], box_b[1])
    right = min(box_a[2], box_b[2])
    bottom = min(box_a[3], box_b[3])
    return max(0.0, right - left) * max(0.0, bottom - top)


def inspect_pdf_layout(path: Path) -> list[str]:
    """Verifica colisões geométricas de texto e sinais de PDF baseado em imagem.

    A análise é deliberadamente conservadora: só sinaliza palavras cujas caixas
    realmente se interceptam, em vez de marcar toda aproximação entre colunas.
    """
    warnings: list[str] = []
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(path)) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                words = page.extract_words(x_tolerance=1, y_tolerance=1, keep_blank_chars=False) or []
                boxes: list[tuple[str, tuple[float, float, float, float]]] = []
                for word in words:
                    text = str(word.get("text", "")).strip()
                    if not text:
                        continue
                    box = (float(word["x0"]), float(word["top"]), float(word["x1"]), float(word["bottom"]))
                    boxes.append((text, box))

                page_alerts = 0
                for (text_a, box_a), (text_b, box_b) in combinations(boxes, 2):
                    area = _intersection_area(box_a, box_b)
                    if area < 0.75:
                        continue
                    width_a = max(1.0, box_a[2] - box_a[0])
                    height_a = max(1.0, box_a[3] - box_a[1])
                    width_b = max(1.0, box_b[2] - box_b[0])
                    height_b = max(1.0, box_b[3] - box_b[1])
                    # Evita falsos positivos de arredondamento/kerning mínimo.
                    if area / min(width_a * height_a, width_b * height_b) < 0.08:
                        continue
                    warnings.append(
                        f"ALERTA DE SOBREPOSIÇÃO — página {page_number}: os elementos “{text_a[:32]}” e “{text_b[:32]}” ocupam a mesma área."
                    )
                    page_alerts += 1
                    if page_alerts >= 3:
                        warnings.append(f"Página {page_number}: há outras possíveis colisões não listadas; revise a imagem original.")
                        break

                image_count = len(getattr(page, "images", []) or [])
                if image_count and len(boxes) < 5:
                    warnings.append(
                        f"ALERTA DE IMAGEM — página {page_number}: foram encontrados {image_count} elemento(s) de imagem e pouco texto pesquisável; a extração pode exigir OCR."
                    )
    except Exception as exc:  # pragma: no cover - depende do PDF
        warnings.append(f"ALERTA DE LAYOUT — não foi possível verificar a geometria deste PDF: {exc}")
    return warnings


def detect_bank(text: str) -> str:
    upper = text.upper()
    for name, profile in BANKS.items():
        if name == "Detecção automática":
            continue
        if any(pattern in upper for pattern in profile["patterns"]):
            return name
    return "Detecção automática"


def parse_date(value: str) -> Optional[date]:
    value = value.strip().replace("-", "/")
    parts = value.split("/")
    try:
        if len(parts) != 3:
            return None
        if len(parts[0]) == 4:
            year, month, day = map(int, parts)
        else:
            day, month, year = map(int, parts)
            if year < 100:
                year += 2000 if year < 70 else 1900
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


def parse_money(value: str) -> Optional[Decimal]:
    """Interpreta formatos brasileiros e internacionais de moeda."""
    raw = value.upper().strip()
    negative = "(" in raw or "-" in raw
    if re.search(r"(?:^|\s)(?:D|DEB|DÉBITO|DEBITO)(?:\s|$)", raw):
        negative = True
    raw = re.sub(r"R\$", "", raw)
    raw = re.sub(r"[^0-9,.-]", "", raw)
    raw = raw.replace("-", "")
    if not raw:
        return None

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        # Um ponto isolado com exatamente duas casas é decimal; os demais são milhares.
        if raw.count(".") > 1 or (raw.count(".") == 1 and len(raw.rsplit(".", 1)[1]) != 2):
            raw = raw.replace(".", "")

    try:
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return -amount if negative and amount > 0 else amount


def _date_from_line(line: str) -> tuple[Optional[date], Optional[re.Match[str]]]:
    match = DATE_RE.search(line)
    if not match:
        return None, None
    return parse_date(match.group(1)), match


def _number_candidates(line: str) -> list[tuple[Decimal, re.Match[str]]]:
    result: list[tuple[Decimal, re.Match[str]]] = []
    for match in NUMBER_RE.finditer(line):
        amount = parse_money(match.group(0))
        if amount is not None:
            result.append((amount, match))
    return result


def _clean_memo(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" -|;:")
    value = re.sub(r"\b(?:C|D|CR|DB|CRED|DEB)\b$", "", value, flags=re.IGNORECASE).strip()
    return value or "Movimentação bancária"


def parse_transactions(text: str) -> tuple[list[Transaction], list[str]]:
    transactions: list[Transaction] = []
    warnings: list[str] = []
    seen: set[tuple[date, str, Decimal]] = set()

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or EXCLUDED_LINE_RE.search(line):
            continue
        parsed_date, date_match = _date_from_line(line)
        if not parsed_date or not date_match:
            continue
        candidates = _number_candidates(line)
        if not candidates:
            continue

        # Em extratos, o último número monetário da linha costuma ser o valor da transação.
        amount, amount_match = candidates[-1]
        before_amount = line[: amount_match.start()]
        after_amount = line[amount_match.end() :]
        memo = _clean_memo(before_amount.replace(date_match.group(0), "", 1) + " " + after_amount)
        if memo.lower() in {"", "-", "saldo"}:
            memo = "Movimentação bancária"

        # Ajusta o sinal quando o banco usa marcador textual após o valor.
        marker = after_amount.strip().upper().split(" ", 1)[0] if after_amount.strip() else ""
        if marker in {"D", "DB", "DEB", "DÉBITO", "DEBITO"} and amount > 0:
            amount = -amount
        elif marker in {"C", "CR", "CRED", "CRÉDITO", "CREDITO"} and amount < 0:
            amount = abs(amount)

        key = (parsed_date, memo.casefold(), amount)
        if key in seen:
            continue
        seen.add(key)
        confidence = "alta" if len(memo) > 5 else "média"
        transactions.append(
            Transaction(
                date=parsed_date,
                memo=memo,
                amount=amount,
                source_line=line,
                confidence=confidence,
            )
        )

    transactions.sort(key=lambda item: (item.date, item.memo.casefold()))
    if not transactions:
        warnings.append(
            "Nenhuma transação foi identificada. O PDF pode ser escaneado, protegido ou usar um layout ainda não mapeado."
        )
    else:
        low_confidence = sum(item.confidence != "alta" for item in transactions)
        if low_confidence:
            warnings.append(f"{low_confidence} transação(ões) exigem conferência do histórico.")
    return transactions, warnings


def parse_pdf(path: Path) -> ParsedDocument:
    layout_warnings = inspect_pdf_layout(path)
    try:
        text = read_pdf_text(path)
        extraction_warnings: list[str] = []
    except ValueError as exc:
        # Mantém o documento na tela para permitir revisão e alerta, em vez de
        # esconder um PDF escaneado/protegido atrás de uma falha genérica.
        text = ""
        extraction_warnings = [f"ALERTA DE EXTRAÇÃO — {exc}"]
    transactions, warnings = parse_transactions(text)
    all_warnings = layout_warnings + extraction_warnings + warnings
    return ParsedDocument(
        source_path=path,
        text=text,
        transactions=transactions,
        detected_bank=detect_bank(text),
        warnings=all_warnings,
        layout_warnings=layout_warnings,
    )


def _fitid(transaction: Transaction, index: int, source_name: str) -> str:
    seed = f"{source_name}|{index}|{transaction.date.isoformat()}|{transaction.memo}|{transaction.amount}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24].upper()


def _ofx_date(value: date) -> str:
    return value.strftime("%Y%m%d000000[-3:BRT]")


def generate_ofx(
    document: ParsedDocument,
    bank_name: str,
    account_type: str = "CHECKING",
    bank_id: str = "",
    branch_id: str = "",
    account_id: str = "",
) -> str:
    transactions = document.transactions
    now = datetime.now()
    start, end = document.date_range
    start = start or now.date()
    end = end or now.date()
    profile = BANKS.get(bank_name, BANKS["Detecção automática"])
    resolved_bank_id = bank_id.strip() or profile["code"]
    resolved_account_id = account_id.strip() or "NAO INFORMADO"
    resolved_branch_id = branch_id.strip() or "NAO INFORMADA"
    uid = str(uuid.uuid4()).upper()

    lines = [
        "OFXHEADER:100",
        "DATA:OFXSGML",
        "VERSION:103",
        "SECURITY:NONE",
        "ENCODING:UTF-8",
        "CHARSET:65001",
        "COMPRESSION:NONE",
        "OLDFILEUID:NONE",
        "NEWFILEUID:" + uid,
        "<OFX>",
        "<SIGNONMSGSRSV1>",
        "<SONRS>",
        "<STATUS><CODE>0<SEVERITY>INFO</STATUS>",
        f"<DTSERVER>{now.strftime('%Y%m%d%H%M%S[-3:BRT]')}",
        "<LANGUAGE>POR",
        f"<FI><ORG>{escape(bank_name or 'PDF2OFX Desktop')}<FID>{escape(resolved_bank_id)}</FI>",
        "</SONRS>",
        "</SIGNONMSGSRSV1>",
        "<BANKMSGSRSV1>",
        "<STMTTRNRS><TRNUID>0",
        "<STATUS><CODE>0<SEVERITY>INFO</STATUS>",
        "<STMTRS>",
        "<CURDEF>BRL",
        "<BANKACCTFROM>",
        f"<BANKID>{escape(resolved_bank_id)}",
        f"<BRANCHID>{escape(resolved_branch_id)}",
        f"<ACCTID>{escape(resolved_account_id)}",
        f"<ACCTTYPE>{escape(account_type)}",
        "</BANKACCTFROM>",
        "<BANKTRANLIST>",
        f"<DTSTART>{_ofx_date(start)}",
        f"<DTEND>{_ofx_date(end)}",
    ]

    for index, transaction in enumerate(transactions, 1):
        signed_amount = format(transaction.amount, ".2f")
        memo = escape(transaction.memo[:250])
        name = escape(transaction.memo[:64])
        trntype = "CREDIT" if transaction.amount >= 0 else "DEBIT"
        lines.extend(
            [
                "<STMTTRN>",
                f"<TRNTYPE>{trntype}",
                f"<DTPOSTED>{_ofx_date(transaction.date)}",
                f"<TRNAMT>{signed_amount}",
                f"<FITID>{_fitid(transaction, index, document.name)}",
                f"<NAME>{name}",
                f"<MEMO>{memo}",
                "</STMTTRN>",
            ]
        )

    lines.extend(
        [
            "</BANKTRANLIST>",
            "</STMTRS>",
            "</STMTTRNRS>",
            "</BANKMSGSRSV1>",
            "</OFX>",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_csv(document: ParsedDocument) -> str:
    rows = ["Data;Histórico;Valor;Tipo"]
    for transaction in document.transactions:
        memo = transaction.memo.replace(";", ",")
        rows.append(
            f"{transaction.date.strftime('%d/%m/%Y')};{memo};{format(transaction.amount, '.2f').replace('.', ',')};{transaction.kind}"
        )
    return "\n".join(rows) + "\n"


def safe_output_name(path: Path) -> str:
    stem = re.sub(r"[^\w\-. ]+", "", path.stem, flags=re.UNICODE).strip() or "extrato"
    return stem


def validate_document(document: ParsedDocument) -> list[str]:
    issues: list[str] = []
    if not document.transactions:
        issues.append("O documento não possui transações identificadas.")
    for index, transaction in enumerate(document.transactions, 1):
        if not transaction.memo or transaction.memo == "Movimentação bancária":
            issues.append(f"Transação {index}: histórico vazio ou genérico.")
        if transaction.amount == 0:
            issues.append(f"Transação {index}: valor igual a zero.")
    return issues
