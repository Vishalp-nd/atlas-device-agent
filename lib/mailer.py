#!/usr/bin/env python3
"""Send an email with a file attached, using SMTP credentials from .env
(SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, FROM_EMAIL, TO_EMAIL).
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"


def _split_recipients(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def send_email_with_attachment(attachment_paths: Path | list[Path], subject: str, body: str,
                                to_emails: list[str] | None = None) -> None:
    load_dotenv(str(ENV_PATH), override=False)

    smtp_server = os.environ["SMTP_SERVER"]
    smtp_port = int(os.environ["SMTP_PORT"])
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    from_email = os.environ["FROM_EMAIL"]
    recipients = to_emails or _split_recipients(os.environ.get("TO_EMAIL", ""))
    if not recipients:
        raise RuntimeError("No recipients configured (TO_EMAIL is empty in .env and --to was not provided).")

    if isinstance(attachment_paths, (str, Path)):
        attachment_paths = [attachment_paths]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    for attachment_path in attachment_paths:
        attachment_path = Path(attachment_path)
        data = attachment_path.read_bytes()
        mime_type, _ = mimetypes.guess_type(attachment_path.name)
        maintype, subtype = mime_type.split("/", 1) if mime_type else ("application", "octet-stream")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=attachment_path.name)

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attachment", required=True, nargs="+", help="Path(s) to the file(s) to attach (e.g. a critical-bugs markdown report). Pass multiple paths separated by spaces to attach more than one.")
    parser.add_argument("--subject", default=None, help="Email subject. Defaults to 'Critical Bug Report: <first attachment filename>'.")
    parser.add_argument("--to", default=None, help="Comma-separated recipient override. Defaults to TO_EMAIL in .env.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    attachment_paths = [Path(p) for p in args.attachment]
    missing = [p for p in attachment_paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"[-] Attachment not found: {p}")
        return 1

    subject = args.subject or f"Critical Bug Report: {attachment_paths[0].name}"
    body = "Attached: " + ", ".join(p.name for p in attachment_paths) + "\n\nThanks,\nAtlas"
    to_emails = _split_recipients(args.to) if args.to else None

    send_email_with_attachment(attachment_paths, subject, body, to_emails)
    print(f"[+] Email sent with attachment(s): {', '.join(p.name for p in attachment_paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
