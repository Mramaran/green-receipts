import os
import threading
from pathlib import Path

from eth_account import Account

DEFAULT_KEY_PATH = Path(__file__).resolve().parent.parent / ".issuer_key"
_lock = threading.Lock()


def load_or_create_issuer_key(path: Path = DEFAULT_KEY_PATH) -> str:
    """Local demo issuer key. Created on first use, readable by the owner only, kept out of git.

    Test networks only. A real deployment needs a key held in a key management service.
    """
    with _lock:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        key = "0x" + bytes(Account.create().key).hex()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key)
        return key
