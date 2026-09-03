from __future__ import annotations


class LLMErrorCode:
    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    BAD_REQUEST = "BAD_REQUEST"
    UNAVAILABLE = "UNAVAILABLE"
    PARSE = "PARSE"


class LLMError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LLMStructuredOutputError(LLMError):
    def __init__(self, message: str, *, retry_count: int = 0) -> None:
        super().__init__(LLMErrorCode.PARSE, message, retryable=False)
        self.retry_count = retry_count


class FallbackDisabled(LLMError):
    def __init__(self) -> None:
        super().__init__(
            LLMErrorCode.UNAVAILABLE,
            "Fallback models are disabled in V1. Pin a single model via experiment.",
            retryable=False,
        )
