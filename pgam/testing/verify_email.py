from __future__ import annotations

import asyncio
import json
import re
import smtplib
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pgam.notifications.email import EmailNotifier, NotificationError, SmtpConfig

SMTP_PORT = 465
RECIPIENT = "2710936865@qq.com"
SMTP_HOSTS = {
    "qq.com": "smtp.qq.com",
    "foxmail.com": "smtp.qq.com",
    "163.com": "smtp.163.com",
    "126.com": "smtp.126.com",
    "sina.com": "smtp.sina.com",
    "gmail.com": "smtp.gmail.com",
    "outlook.com": "smtp-mail.outlook.com",
}


@dataclass(slots=True)
class EmailCredentials:
    account: str
    password: str

    @property
    def sender(self) -> str:
        return self.account if "@" in self.account else f"{self.account}@qq.com"


def _read_credentials(project_root: Path) -> EmailCredentials:
    account_path = project_root / "email_account.txt"
    password_path = project_root / "email_password.txt"
    account = account_path.read_text(encoding="utf-8").strip().lstrip("\ufeff").strip()
    password = password_path.read_text(encoding="utf-8").strip().lstrip("\ufeff").strip()

    if not account or any(character in account for character in "\r\n\t "):
        raise ValueError("email_account.txt must contain one email account without extra whitespace")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+", account):
        raise ValueError("email_account.txt does not contain a valid email address")
    domain = account.rsplit("@", 1)[1].lower()
    if domain not in SMTP_HOSTS:
        raise ValueError("The email provider is not supported by this manual verification script")
    if not password or any(character in password for character in "\r\n\t "):
        raise ValueError("email_password.txt must contain one email password/authorization code without extra whitespace")
    return EmailCredentials(account=account, password=password)


def _verify_smtp_tls(credentials: EmailCredentials, smtp_host: str) -> None:
    """Verify the SMTP endpoint and certificate before attempting message submission."""
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, SMTP_PORT, timeout=20, context=context) as client:
        client.ehlo()
        client.login(credentials.account, credentials.password)


def verify(project_root: Path) -> dict[str, Any]:
    credentials = _read_credentials(project_root)
    smtp_host = SMTP_HOSTS[credentials.sender.rsplit("@", 1)[1].lower()]
    _verify_smtp_tls(credentials, smtp_host)
    notifier = EmailNotifier(
        SmtpConfig(
            host=smtp_host,
            port=SMTP_PORT,
            username=credentials.account,
            password=credentials.password,
            sender=credentials.sender,
            recipient=RECIPIENT,
            use_tls=True,
            subject_prefix="\u3010\u62db\u751f\u76d1\u89c6\u3011",
        )
    )
    asyncio.run(notifier.send_test())
    return {
        "smtp_host": smtp_host,
        "smtp_port": SMTP_PORT,
        "smtp_ssl": True,
        "recipient": RECIPIENT,
        "authentication_passed": True,
        "message_accepted_by_smtp_server": True,
        "credentials_printed": False,
        "all_passed": True,
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    try:
        result = verify(project_root)
    except smtplib.SMTPAuthenticationError as exc:
        print(
            json.dumps(
                {
                    "all_passed": False,
                    "error_type": type(exc).__name__,
                    "smtp_code": exc.smtp_code,
                    "error": "SMTP authentication failed; the provider likely requires an SMTP authorization code instead of the web login password.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    except NotificationError as exc:
        print(
            json.dumps(
                {
                    "all_passed": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    except Exception as exc:
        # Do not include credential values or potentially sensitive SMTP diagnostics.
        print(
            json.dumps(
                {
                    "all_passed": False,
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
