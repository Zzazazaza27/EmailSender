#!/usr/bin/env python3
import argparse
import socket
import sys
import time
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Iterable, Optional

import dns.resolver
import dns.exception


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
        # Try A/AAAA. If NXDOMAIN -> domain absent.
        resolver.resolve(domain, "A")
        return True
    except dns.resolver.NXDOMAIN:
        return False
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        # If A fails, try AAAA (some domains are IPv6-only)
        try:
            resolver.resolve(domain, "AAAA")
            return True
        except dns.resolver.NXDOMAIN:
            return False
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
            # Domain might still exist but not have A/AAAA; treat as exists and rely on MX check next.
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
    # Best-effort: many providers disable VRFY and may accept all RCPT (catch-all) or use greylisting.
    # Return: accepted | rejected | unknown
    import smtplib

    last_error: Optional[str] = None

    for host in mx_hosts:
        try:
            with smtplib.SMTP(host=host, port=25, timeout=timeout_s) as smtp:
                smtp.set_debuglevel(0)
                smtp.ehlo_or_helo_if_needed()

                # Some servers require a known HELO; use provided.
                try:
                    smtp.helo(name=helo_host)
                except smtplib.SMTPHeloError:
                    pass

                code, _ = smtp.mail(mail_from)
                if code and code >= 400:
                    # try next host
                    last_error = f"MAIL FROM rejected ({code})"
                    continue

                code, _ = smtp.rcpt(email)

                if code is None:
                    return "unknown"

                if 200 <= code < 300:
                    return "accepted"

                if 500 <= code < 600:
                    return "rejected"

                # 4xx / others => temporary / greylist
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
    local, domain = email.rsplit("@", 1)

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Email domain/MX + SMTP handshake checker")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a text file with emails (one per line)",
    )
    parser.add_argument("--dns-timeout", type=float, default=4.0)
    parser.add_argument("--smtp-timeout", type=float, default=6.0)
    parser.add_argument("--no-smtp", action="store_true", help="Skip SMTP handshake step")
    parser.add_argument("--helo-host", default="localhost")
    parser.add_argument("--mail-from", default="no-reply@example.com")
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep between checks (seconds)")

    args = parser.parse_args()

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

        print(
            f"{res.email}\t{res.status}\tmx=[{mx_preview}]\tsmtp={res.smtp_result}",
            flush=True,
        )

        if args.sleep > 0:
            time.sleep(args.sleep)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
