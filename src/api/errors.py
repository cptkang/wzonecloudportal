"""예외 → HTTP 매핑 (계획 08 §3.2).

**500 응답에 내부 상세를 넣지 않는다.** 스택·SQL·경로가 노출되면 공격 표면이 된다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.domain.exceptions import (
    AuthenticationError,
    CollectionError,
    DuplicateError,
    NotFoundError,
    PermissionError as DomainPermissionError,
    PortalError,
    ValidationError,
)

logger = logging.getLogger(__name__)

EXCEPTION_STATUS: list[tuple[type[Exception], int, str]] = [
    (ValidationError, 422, "validation_error"),
    (AuthenticationError, 401, "authentication_failed"),
    (DomainPermissionError, 403, "permission_denied"),
    (NotFoundError, 404, "not_found"),
    (DuplicateError, 409, "duplicate"),
    (CollectionError, 502, "collection_error"),
]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PortalError)
    async def _handle(request: Request, exc: PortalError) -> JSONResponse:  # noqa: ARG001
        for exc_type, status, code in EXCEPTION_STATUS:
            if isinstance(exc, exc_type):
                detail = dict(exc.detail)
                field = getattr(exc, "field", None)
                if field:
                    detail["field"] = field
                return JSONResponse(
                    status_code=status,
                    content={"error": code, "message": exc.message, "detail": detail},
                )
        logger.exception("처리되지 않은 도메인 오류")
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "처리 중 오류가 발생했습니다.",
                "detail": {},
            },
        )
