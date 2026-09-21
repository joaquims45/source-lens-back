import re

PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
TOKENS = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b"
)
ASSIGNMENT = re.compile(
    r"""(?im)((?:password|passwd|secret|api[_-]?key|access[_-]?token)[\w-]*["']?\s*[:=]\s*["'])([^"'\r\n]+)"""
)


def mask(value: str) -> str:
    return "".join(char if char in "\r\n" else "*" for char in value)


def redact(content: str) -> tuple[str, int]:
    count = 0
    for pattern in (PRIVATE_KEY, TOKENS):
        content, matches = pattern.subn(lambda match: mask(match.group()), content)
        count += matches
    content, matches = ASSIGNMENT.subn(lambda match: match[1] + mask(match[2]), content)
    return content, count + matches
