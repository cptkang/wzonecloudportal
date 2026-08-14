"""로그·예외 메시지 마스킹 (계획 10 §5).

**서드파티 라이브러리를 신뢰하지 않는다.** pyVmomi·pypsrp가 예외 메시지나 디버그
로그에 접속 정보를 넣을 수 있다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

MASK = "***"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # password: xxx / password=xxx / "password": "xxx"
    (
        re.compile(
            r"""(['"]?(?:password|pwd|passwd|secret|token|api[_-]?key)['"]?\s*[:=]\s*['"]?)([^'"\s,;}&]+)""",
            re.IGNORECASE,
        ),
        r"\1" + MASK,
    ),
    # URL 내 자격증명 — scheme://user:pass@host
    (re.compile(r"(://[^:/@\s]+:)([^@\s]+)(@)"), r"\1" + MASK + r"\3"),
    # Authorization 헤더
    (re.compile(r"(Authorization:\s*\w+\s+)(\S+)", re.IGNORECASE), r"\1" + MASK),
    # Basic 인증 base64
    (re.compile(r"(Basic\s+)([A-Za-z0-9+/=]{8,})"), r"\1" + MASK),
)

#: LogRecord의 표준 속성 — 마스킹 대상에서 제외한다
_RESERVED = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_text", "stack_info", "lineno", "funcName", "created",
        "msecs", "relativeCreated", "thread", "threadName", "processName",
        "process", "taskName",
    }
)


def mask_text(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def sanitize_message(text: str, *, secrets: Iterable[str] = ()) -> str:
    """패턴 마스킹 + 알려진 비밀값 직접 제거."""
    out = mask_text(text)
    for s in secrets:
        if s and len(s) >= 4:
            out = out.replace(s, MASK)
    return out


def sanitize_error(exc: BaseException, *, secrets: Iterable[str] = ()) -> str:
    """예외를 사용자에게 보여줄 문자열로 정제한다."""
    return sanitize_message(str(exc) or type(exc).__name__, secrets=secrets)


class CredentialMaskingFilter(logging.Filter):
    """모든 로그 레코드에서 자격증명 패턴을 마스킹한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_text(record.msg)
        if record.args:
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            record.args = tuple(mask_text(a) if isinstance(a, str) else a for a in args)
        for key, val in list(vars(record).items()):
            if isinstance(val, str) and key not in _RESERVED:
                setattr(record, key, mask_text(val))
        return True


def install_masking(handlers: Iterable[logging.Handler]) -> None:
    """필터를 **로거가 아니라 핸들러**에 부착한다.

    필터는 로거 단위로 상속되지 않으므로, 핸들러에 붙여야 하위 로거의 레코드까지
    통과한다 (계획 10 §5.1).
    """
    f = CredentialMaskingFilter()
    for h in handlers:
        h.addFilter(f)
