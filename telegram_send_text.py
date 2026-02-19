#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip("\n")
    except FileNotFoundError as e:
        raise FileNotFoundError(f"File not found: {path}") from e


def send_message(bot_token: str, chat_id: str, text: str, timeout_s: float) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Send text from .txt file to a private Telegram chat via bot")
    parser.add_argument("--file", required=True, help="Path to .txt file")
    parser.add_argument(
        "--bot-token",
        default=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var)",
    )
    parser.add_argument(
        "--chat-id",
        default=os.getenv("TELEGRAM_CHAT_ID", ""),
        help="Target chat_id (or set TELEGRAM_CHAT_ID env var)",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not send message; only print what would be sent",
    )

    args = parser.parse_args()

    if not args.bot_token:
        print("Missing --bot-token (or TELEGRAM_BOT_TOKEN)", file=sys.stderr)
        return 2

    if not args.chat_id:
        print("Missing --chat-id (or TELEGRAM_CHAT_ID)", file=sys.stderr)
        return 2

    try:
        text = _read_text(args.file)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not text.strip():
        print("Input file is empty", file=sys.stderr)
        return 2

    if args.dry_run:
        preview = text if len(text) <= 500 else text[:500] + "..."
        print("DRY RUN")
        print(f"chat_id={args.chat_id}")
        print(f"text_preview={preview!r}")
        return 0

    send_message(args.bot_token, args.chat_id, text, timeout_s=args.timeout)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
