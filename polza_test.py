#!/usr/bin/env python3
import argparse
import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Iterable, Optional

import dns.exception
import dns.resolver


@dataclass(frozen=True)
class EmailCheckResult:
    email: str
    status: str
    domain: str
    mx_hosts: tuple[str, ...]
    smtp_result: str


def _make_resolver(timeout_s: float) -> dns.resolver.Resolver:
    try:
        resolver = dns.resolver.Resolver(configure=True)
    except dns.resolver.NoResolverConfiguration:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = ["1.1.1.1", "8.8.8.8"]

    resolver.lifetime = timeout_s
    return resolver


def _extract_email(raw: str) -> Optional[str]:
    raw = raw.strip()
    if not raw:
        return None

    _, addr = parseaddr(raw)
    addr = (addr or raw).strip()
    addr = addr.strip("\"'<>[](){}.,;:")

    if "@" not in addr:
        return None

    local, domain = addr.rsplit("@", 1)
    if not local or not domain:
        return None

    return f"{local}@{domain}".lower()


def _domain_exists(domain: str, timeout_s: float) -> bool:
    resolver = _make_resolver(timeout_s=timeout_s)

    try:
        resolver.resolve(domain, "A")
        return True
    except dns.resolver.NXDOMAIN:
        return False
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        try:
            resolver.resolve(domain, "AAAA")
            return True
        except dns.resolver.NXDOMAIN:
            return False
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
            return True


def _resolve_mx(domain: str, timeout_s: float) -> tuple[str, ...]:
    resolver = _make_resolver(timeout_s=timeout_s)

    try:
        answers = resolver.resolve(domain, "MX")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return tuple()

    mx = []
    for rdata in answers:
        host = str(rdata.exchange).rstrip(".").lower()
        if host:
            mx.append((rdata.preference, host))

    mx.sort(key=lambda x: x[0])
    return tuple(h for _, h in mx)


def _smtp_handshake_check(
    email: str,
    mx_hosts: Iterable[str],
    timeout_s: float,
    helo_host: str,
    mail_from: str,
) -> str:
    import smtplib

    last_error: Optional[str] = None

    for host in mx_hosts:
        try:
            with smtplib.SMTP(host=host, port=25, timeout=timeout_s) as smtp:
                smtp.set_debuglevel(0)
                smtp.ehlo_or_helo_if_needed()

                try:
                    smtp.helo(name=helo_host)
                except smtplib.SMTPHeloError:
                    pass

                code, _ = smtp.mail(mail_from)
                if code and code >= 400:
                    last_error = f"MAIL FROM rejected ({code})"
                    continue

                code, _ = smtp.rcpt(email)

                if code is None:
                    return "unknown"

                if 200 <= code < 300:
                    return "accepted"

                if 500 <= code < 600:
                    return "rejected"

                return "unknown"

        except (socket.timeout, OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected) as e:
            last_error = f"connect error: {e.__class__.__name__}"
            continue
        except smtplib.SMTPException as e:
            last_error = f"smtp error: {e.__class__.__name__}"
            continue

    if last_error:
        return "unknown"

    return "unknown"


def check_email(
    email: str,
    dns_timeout_s: float,
    smtp_timeout_s: float,
    helo_host: str,
    mail_from: str,
    do_smtp: bool,
) -> EmailCheckResult:
    _, domain = email.rsplit("@", 1)

    if not _domain_exists(domain, timeout_s=dns_timeout_s):
        return EmailCheckResult(
            email=email,
            status="домен отсутствует",
            domain=domain,
            mx_hosts=tuple(),
            smtp_result="skipped",
        )

    mx_hosts = _resolve_mx(domain, timeout_s=dns_timeout_s)
    if not mx_hosts:
        return EmailCheckResult(
            email=email,
            status="MX-записи отсутствуют или некорректны",
            domain=domain,
            mx_hosts=tuple(),
            smtp_result="skipped",
        )

    smtp_result = "skipped"
    if do_smtp:
        smtp_result = _smtp_handshake_check(
            email=email,
            mx_hosts=mx_hosts,
            timeout_s=smtp_timeout_s,
            helo_host=helo_host,
            mail_from=mail_from,
        )

    return EmailCheckResult(
        email=email,
        status="домен валиден",
        domain=domain,
        mx_hosts=mx_hosts,
        smtp_result=smtp_result,
    )


def _iter_emails_from_file(path: str) -> list[str]:
    out: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            addr = _extract_email(line)
            if addr:
                out.append(addr)
    return out


def telegram_send_message(bot_token: str, chat_id: str, text: str, timeout_s: float) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"Telegram API returned non-JSON response: {raw[:200]}")

    if not parsed.get("ok"):
        raise RuntimeError(f"Telegram API error: {parsed}")


def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip("\n")
    except FileNotFoundError as e:
        raise FileNotFoundError(f"File not found: {path}") from e


def cmd_email_check(args: argparse.Namespace) -> int:
    emails = _iter_emails_from_file(args.input)
    if not emails:
        print("No valid emails found in input", file=sys.stderr)
        return 2

    for e in emails:
        res = check_email(
            email=e,
            dns_timeout_s=args.dns_timeout,
            smtp_timeout_s=args.smtp_timeout,
            helo_host=args.helo_host,
            mail_from=args.mail_from,
            do_smtp=not args.no_smtp,
        )

        mx_preview = ",".join(res.mx_hosts[:3])
        if len(res.mx_hosts) > 3:
            mx_preview += ",..."

        print(f"{res.email}\t{res.status}\tmx=[{mx_preview}]\tsmtp={res.smtp_result}", flush=True)

        if args.sleep > 0:
            time.sleep(args.sleep)

    return 0


def cmd_telegram_send(args: argparse.Namespace) -> int:
    bot_token = args.bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = args.chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    if not bot_token:
        print("Missing --bot-token (or TELEGRAM_BOT_TOKEN)", file=sys.stderr)
        return 2

    if not chat_id:
        print("Missing --chat-id (or TELEGRAM_CHAT_ID)", file=sys.stderr)
        return 2

    try:
        text = _read_text_file(args.file)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2

    if not text.strip():
        print("Input file is empty", file=sys.stderr)
        return 2

    if args.dry_run:
        preview = text if len(text) <= 500 else text[:500] + "..."
        print("DRY RUN")
        print(f"chat_id={chat_id}")
        print(f"text_preview={preview!r}")
        return 0

    telegram_send_message(bot_token=bot_token, chat_id=chat_id, text=text, timeout_s=args.timeout)
    print("OK")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    email_args = argparse.Namespace(
        input=args.emails_input,
        dns_timeout=args.dns_timeout,
        smtp_timeout=args.smtp_timeout,
        no_smtp=args.no_smtp,
        helo_host=args.helo_host,
        mail_from=args.mail_from,
        sleep=args.sleep,
    )
    code_email = cmd_email_check(email_args)
    if code_email != 0:
        return code_email

    tg_args = argparse.Namespace(
        file=args.message_file,
        bot_token=args.bot_token,
        chat_id=args.chat_id,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    return cmd_telegram_send(tg_args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Polza test: email domain/MX/SMTP + telegram send")
    sub = parser.add_subparsers(dest="command", required=True)

    p_email = sub.add_parser("email-check", help="Check email domains, MX, and do SMTP handshake")
    p_email.add_argument("--input", required=True, help="Path to a text file with emails (one per line)")
    p_email.add_argument("--dns-timeout", type=float, default=4.0)
    p_email.add_argument("--smtp-timeout", type=float, default=6.0)
    p_email.add_argument("--no-smtp", action="store_true", help="Skip SMTP handshake step")
    p_email.add_argument("--helo-host", default="localhost")
    p_email.add_argument("--mail-from", default="no-reply@example.com")
    p_email.add_argument("--sleep", type=float, default=0.0, help="Sleep between checks (seconds)")
    p_email.set_defaults(func=cmd_email_check)

    p_tg = sub.add_parser("telegram-send", help="Send text from .txt file to Telegram chat")
    p_tg.add_argument("--file", required=True, help="Path to .txt file")
    p_tg.add_argument("--bot-token", default="", help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var)")
    p_tg.add_argument("--chat-id", default="", help="Target chat_id (or set TELEGRAM_CHAT_ID env var)")
    p_tg.add_argument("--timeout", type=float, default=10.0)
    p_tg.add_argument("--dry-run", action="store_true", help="Do not send; only print preview")
    p_tg.set_defaults(func=cmd_telegram_send)

    p_all = sub.add_parser("all", help="Run email-check and telegram-send sequentially")
    p_all.add_argument("--emails-input", required=True, help="Path to a text file with emails (one per line)")
    p_all.add_argument("--dns-timeout", type=float, default=4.0)
    p_all.add_argument("--smtp-timeout", type=float, default=6.0)
    p_all.add_argument("--no-smtp", action="store_true", help="Skip SMTP handshake step")
    p_all.add_argument("--helo-host", default="localhost")
    p_all.add_argument("--mail-from", default="no-reply@example.com")
    p_all.add_argument("--sleep", type=float, default=0.0, help="Sleep between checks (seconds)")
    p_all.add_argument("--message-file", required=True, help="Path to .txt file")
    p_all.add_argument("--bot-token", default="", help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var)")
    p_all.add_argument("--chat-id", default="", help="Target chat_id (or set TELEGRAM_CHAT_ID env var)")
    p_all.add_argument("--timeout", type=float, default=10.0)
    p_all.add_argument("--dry-run", action="store_true", help="Do not send; only print preview")
    p_all.set_defaults(func=cmd_all)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
