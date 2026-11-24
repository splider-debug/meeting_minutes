# -*- coding: utf-8 -*-
"""统一错误类型。code 与技术方案 §九"错误码表"一一对应，前端可直接映射文案。"""


class AppError(Exception):
    code = "APP_ERROR"
    http_status = 500
    retryable = False

    def __init__(self, message: str, code: str | None = None,
                 retryable: bool | None = None, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "retryable": self.retryable, "detail": self.detail}


class FileTooLarge(AppError):
    code = "FILE_TOO_LARGE"
    http_status = 413


class UnsupportedType(AppError):
    code = "UNSUPPORTED_TYPE"
    http_status = 415


class TypeMismatch(AppError):
    code = "TYPE_MISMATCH"
    http_status = 415


class ParseError(AppError):
    code = "PARSE_ERROR"
    http_status = 422


class OcrUnavailable(AppError):
    code = "OCR_UNAVAILABLE"
    http_status = 501


class RenderError(AppError):
    code = "RENDER_ERROR"
    http_status = 500


class VerifyError(AppError):
    code = "VERIFY_FAILED"
    http_status = 500
