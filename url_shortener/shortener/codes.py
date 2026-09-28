import re
import secrets
import string

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7  # 62^7 ≈ 3.5e12 codes; collision odds stay negligible well past a billion links
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
RESERVED = {"links", "healthz", "docs", "openapi.json", "redoc"}


def new_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def valid_alias(alias: str) -> bool:
    return bool(ALIAS_RE.fullmatch(alias)) and alias.lower() not in RESERVED
