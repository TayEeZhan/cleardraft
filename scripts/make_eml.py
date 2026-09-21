"""Build real .eml sample files for the "upload your own email" demo.

    python scripts/make_eml.py

Picks six emails out of the organiser dataset (data/inbox/*.json plus
data/attachments/) and the held-out challenge set (data/challenge/emails.jsonl),
chosen by outcome so a demo click can show a MISMATCH, a clean pass, an
escalation, a non-comparison email, and an unseen email - and writes each one
as a genuine RFC 5322 .eml file (stdlib `email.message.EmailMessage`, so it
round-trips through adapters/eml.py exactly like a real inbox export) into
web/samples/eml/, plus a manifest.json the frontend can read to list them.

Nothing here is scored and nothing here touches core/. This script only
reads the existing dataset and writes fixtures; it writes nothing back into
data/.
"""
from __future__ import annotations

import email.utils
import json
import mimetypes
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INBOX = os.path.join(DATA, "inbox")
ATTACHMENTS = os.path.join(DATA, "attachments")
CHALLENGE = os.path.join(DATA, "challenge", "emails.jsonl")
OUT_DIR = os.path.join(ROOT, "web", "samples", "eml")

#: (email_id or challenge id, output filename, label, fixed send date).
#: Chosen by outcome against web/public/data.json's board (status/category):
#:   email_004 - MISMATCH, .txt SI/BL                     (required)
#:   email_313 - MISMATCH, .pdf SI/BL - a different format from email_004
#:   email_001 - OK, BL_COMPARISON, .txt SI/BL - a clean pass
#:   email_208 - NEEDS_REVIEW / missing_value, .pdf SI/BL - an escalation
#:   email_002 - INVOICE_QUERY, no attachments - a non-comparison email
#:   ch_01     - data/challenge/emails.jsonl, held-out, no attachments bundled
_DATASET_PICKS = (
    ("email_004", "01_mismatch_email_004.eml",
     "Mismatch: consignee and notify party differ (SI vs draft BL, .txt)"),
    ("email_313", "02_mismatch_pdf_email_313.eml",
     "Mismatch: container count and gross weight differ (SI vs draft BL, .pdf)"),
    ("email_001", "03_cleared_email_001.eml",
     "Clean pass: all 7 fields match (SI vs draft BL, .txt)"),
    ("email_208", "04_needs_review_email_208.eml",
     "Needs review: a required field is missing or blank (SI vs draft BL, .pdf)"),
    ("email_002", "05_nonbl_invoice_email_002.eml",
     "Not a comparison: an invoice query with no attachments"),
)
_CHALLENGE_PICK = ("ch_01", "06_challenge_ch_01.eml",
                    "Held-out set: an unseen email format, no attachments bundled")

#: A stable, readable Date header per sample so the demo does not depend on
#: when this script happens to run.
_DATES = {
    "email_004": "2026-01-14 09:12:00",
    "email_313": "2026-02-03 14:47:00",
    "email_001": "2026-01-08 11:05:00",
    "email_208": "2026-01-22 16:30:00",
    "email_002": "2026-01-16 08:55:00",
    "ch_01": "2026-03-01 10:00:00",
}


def _load_inbox_email(email_id: str) -> dict:
    path = os.path.join(INBOX, f"{email_id}.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_challenge_email(challenge_id: str) -> dict:
    with open(CHALLENGE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("id") == challenge_id:
                return record
    raise KeyError(f"{challenge_id!r} not found in {CHALLENGE}")


def _content_type_for(filename: str) -> "tuple[str, str]":
    guessed, _ = mimetypes.guess_type(filename)
    if not guessed:
        return "application", "octet-stream"
    maintype, _, subtype = guessed.partition("/")
    return maintype, subtype


def _build_message(*, sender: str, subject: str, body: str, date_str: str,
                    attachment_rel_paths: "list[str]") -> "bytes":
    import datetime as _dt
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "ops@example.com"
    msg["Subject"] = subject
    when = _dt.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    msg["Date"] = email.utils.format_datetime(when)
    msg["Message-ID"] = email.utils.make_msgid(domain="cleardraft.demo")
    msg.set_content(body)

    for rel in attachment_rel_paths:
        path = os.path.join(DATA, rel)
        with open(path, "rb") as fh:
            content = fh.read()
        maintype, subtype = _content_type_for(rel)
        msg.add_attachment(
            content, maintype=maintype, subtype=subtype,
            filename=os.path.basename(rel),
        )

    return bytes(msg)


def _write(path: str, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest_files = []

    for email_id, out_name, label in _DATASET_PICKS:
        record = _load_inbox_email(email_id)
        raw = _build_message(
            sender=str(record.get("from", "")),
            subject=str(record.get("subject", "")),
            body=str(record.get("body", "")),
            date_str=_DATES[email_id],
            attachment_rel_paths=list(record.get("attachments") or []),
        )
        out_path = os.path.join(OUT_DIR, out_name)
        _write(out_path, raw)
        manifest_files.append({"name": out_name, "label": label})
        print(f"wrote {out_path} ({len(raw)} bytes, {email_id})")

    challenge_id, out_name, label = _CHALLENGE_PICK
    record = _load_challenge_email(challenge_id)
    raw = _build_message(
        sender="ops-desk@example-carrier.com",
        subject=str(record.get("subject", "")),
        body=str(record.get("body", "")),
        date_str=_DATES[challenge_id],
        attachment_rel_paths=[],
    )
    out_path = os.path.join(OUT_DIR, out_name)
    _write(out_path, raw)
    manifest_files.append({"name": out_name, "label": label})
    print(f"wrote {out_path} ({len(raw)} bytes, {challenge_id})")

    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"files": manifest_files}, fh, indent=2)
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
