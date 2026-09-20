"""Bound multipart bytes before Starlette buffers/spools the upload.

4 MiB of file data plus 64 KiB multipart overhead fits the 6 MiB Lambda invocation envelope
even when base64 encoded. Larger datasets are not supported by this buffered upload endpoint.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sentiment_prep.errors import AppError
from sentiment_prep.sources.csv_upload import MAX_UPLOAD_BYTES

MAX_UPLOAD_BODY_BYTES = MAX_UPLOAD_BYTES + 65536


class PayloadTooLargeError(AppError):
    """Reject a body before retaining bytes past the limit."""

    status_code = 413


class UploadLimitMiddleware:
    """Count actual streamed bytes; Content-Length and MIME metadata are not trusted."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != "/dataset/upload":
            await self.app(scope, receive, send)
            return
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > MAX_UPLOAD_BODY_BYTES:
                raise PayloadTooLargeError("Upload body exceeds the 4 MiB file limit")
            return message

        try:
            await self.app(scope, limited_receive, send)
        except PayloadTooLargeError as exc:
            await JSONResponse({"error": exc.message}, status_code=413)(scope, receive, send)
