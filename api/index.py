from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, jsonify, request, send_file  # noqa: E402

from converter_core import (  # noqa: E402
    BANKS,
    ParsedDocument,
    Transaction,
    generate_ofx,
    parse_pdf,
    safe_output_name,
    validate_document,
)

app = Flask(__name__)

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


@app.route("/api/banks", methods=["GET"])
def banks():
    return jsonify([{"name": name, "code": profile["code"]} for name, profile in BANKS.items()])


@app.route("/api/parse", methods=["POST"])
def parse():
    uploaded = request.files.get("pdf")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Nenhum arquivo PDF enviado."}), 400
    if not uploaded.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Envie um arquivo PDF."}), 400

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            uploaded.save(tmp.name)
            tmp_path = Path(tmp.name)
        document = parse_pdf(tmp_path)
    except Exception as exc:  # pragma: no cover - depende do PDF enviado
        return jsonify({"error": f"Não foi possível ler o PDF: {exc}"}), 400
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return jsonify(
        {
            "name": uploaded.filename,
            "detected_bank": document.detected_bank,
            "warnings": document.warnings,
            "layout_warnings": document.layout_warnings,
            "transactions": [
                {"date": t.date.isoformat(), "memo": t.memo, "amount": str(t.amount)}
                for t in document.transactions
            ],
        }
    )


def _transaction_from_json(item: dict) -> Transaction:
    parsed_date = date.fromisoformat(item["date"])
    amount = Decimal(str(item["amount"])).quantize(Decimal("0.01"))
    memo = str(item.get("memo", "")).strip() or "Movimentação bancária"
    return Transaction(date=parsed_date, memo=memo, amount=amount)


@app.route("/api/generate", methods=["POST"])
def generate():
    payload = request.get_json(silent=True) or {}
    source_name = str(payload.get("source_name") or "extrato.pdf")
    bank_name = str(payload.get("bank_name") or "Detecção automática")
    account_type = str(payload.get("account_type") or "CHECKING")
    bank_id = str(payload.get("bank_id") or "")
    branch_id = str(payload.get("branch_id") or "")
    account_id = str(payload.get("account_id") or "")
    raw_transactions = payload.get("transactions") or []

    try:
        transactions = [_transaction_from_json(item) for item in raw_transactions]
    except (KeyError, ValueError, InvalidOperation) as exc:
        return jsonify({"error": f"Dados de transação inválidos: {exc}"}), 400

    document = ParsedDocument(
        source_path=Path(source_name),
        text="",
        transactions=transactions,
        detected_bank=bank_name,
    )
    issues = validate_document(document)
    if issues:
        return jsonify({"error": "Revise antes de exportar.", "issues": issues}), 400

    ofx_content = generate_ofx(document, bank_name, account_type, bank_id, branch_id, account_id)
    return jsonify({"filename": f"{safe_output_name(document.source_path)}.ofx", "ofx": ofx_content})


@app.route("/api/generate_batch", methods=["POST"])
def generate_batch():
    payload = request.get_json(silent=True) or {}
    bank_name = str(payload.get("bank_name") or "Detecção automática")
    account_type = str(payload.get("account_type") or "CHECKING")
    bank_id = str(payload.get("bank_id") or "")
    branch_id = str(payload.get("branch_id") or "")
    account_id = str(payload.get("account_id") or "")
    raw_documents = payload.get("documents") or []

    if not raw_documents:
        return jsonify({"error": "Nenhum documento para exportar."}), 400

    buffer = io.BytesIO()
    skipped: list[str] = []
    used_names: set[str] = set()
    exported = 0

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for raw_doc in raw_documents:
            source_name = str(raw_doc.get("source_name") or "extrato.pdf")
            try:
                transactions = [_transaction_from_json(item) for item in raw_doc.get("transactions") or []]
            except (KeyError, ValueError, InvalidOperation) as exc:
                skipped.append(f"{source_name}: dados de transação inválidos ({exc})")
                continue

            document = ParsedDocument(
                source_path=Path(source_name),
                text="",
                transactions=transactions,
                detected_bank=bank_name,
            )
            issues = validate_document(document)
            if issues:
                skipped.append(f"{source_name}: " + " ".join(issues[:2]))
                continue

            ofx_content = generate_ofx(document, bank_name, account_type, bank_id, branch_id, account_id)
            base_name = safe_output_name(document.source_path)
            filename = f"{base_name}.ofx"
            suffix = 2
            while filename in used_names:
                filename = f"{base_name}_{suffix}.ofx"
                suffix += 1
            used_names.add(filename)
            zf.writestr(filename, ofx_content)
            exported += 1

        if skipped:
            zf.writestr("ERROS.txt", "Documentos não exportados:\n\n" + "\n".join(skipped))

    if exported == 0:
        return jsonify({"error": "Nenhum documento pôde ser exportado.", "issues": skipped}), 400

    buffer.seek(0)
    response = send_file(buffer, mimetype="application/zip", as_attachment=True, download_name="extratos_ofx.zip")
    response.headers["X-Exported-Count"] = str(exported)
    response.headers["X-Skipped-Count"] = str(len(skipped))
    response.headers["Access-Control-Expose-Headers"] = "X-Exported-Count, X-Skipped-Count"
    return response
