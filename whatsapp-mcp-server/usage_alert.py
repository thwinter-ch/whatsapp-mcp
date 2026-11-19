"""Send one Telegram message per transcribed voice note, with its cost.

Optional: runs only when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. The
monthly total lives in a small JSON ledger next to messages.db. A failure here
never fails the transcription.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import requests

# ElevenLabs Scribe batch price, USD per audio hour (v2 and v3 cost the same).
DEFAULT_USD_PER_HOUR = 0.20


def _chat_name(db_path: str, chat_jid: str) -> str:
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            row = connection.execute("SELECT name FROM chats WHERE jid = ?", (chat_jid,)).fetchone()
        finally:
            connection.close()
        if row and row[0]:
            return row[0]
    except sqlite3.Error:
        pass
    return chat_jid.split("@")[0][-4:].rjust(4, "…")


def _add_to_ledger(path: Path, seconds: float, usd: float) -> dict:
    month = datetime.now().strftime("%Y-%m")
    try:
        ledger = json.loads(path.read_text())
    except (OSError, ValueError):
        ledger = {}
    entry = ledger.setdefault(month, {"notes": 0, "seconds": 0.0, "usd": 0.0})
    entry["notes"] += 1
    entry["seconds"] += seconds
    entry["usd"] += usd
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1))
    tmp.replace(path)
    return entry


def send(db_path: str, chat_jid: str, meta: dict) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("usage_alert: skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)", file=sys.stderr, flush=True)
        return False
    try:
        seconds = float(meta.get("seconds") or 0)
        rate = float(os.getenv("WHATSAPP_TRANSCRIPTION_USD_PER_HOUR", DEFAULT_USD_PER_HOUR))
        usd = seconds / 3600 * rate
        ledger = Path(os.getenv("WHATSAPP_TRANSCRIPTION_LEDGER", str(Path(db_path).with_name("transcription-cost.json"))))
        month = _add_to_ledger(ledger, seconds, usd)
        text = (
            f"🎙️ WhatsApp voice note transcribed\n"
            f"From: {_chat_name(db_path, chat_jid)}\n"
            f"Length: {int(seconds // 60)}:{int(seconds % 60):02d} · language {meta.get('language') or '?'}\n"
            f"Engine: ElevenLabs {meta.get('model')}\n"
            f"Cost: ${usd:.4f}\n"
            f"This month: {month['notes']} notes · {month['seconds'] / 60:.1f} min · ${month['usd']:.3f}"
        )
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
                timeout=10,
            )
        print(f"usage_alert: Telegram {'sent' if response.ok else 'failed'} (HTTP {response.status_code})", file=sys.stderr, flush=True)
        return response.ok
    except Exception as exc:  # never fail a transcription over an alert
        print(f"usage_alert: failed ({type(exc).__name__}: {str(exc)[:120]})", file=sys.stderr, flush=True)
        return False
