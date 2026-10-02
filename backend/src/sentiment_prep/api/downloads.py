"""Local file responses and private cloud downloads that bypass Lambda payload limits."""

from typing import Any

from fastapi import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sentiment_prep.api import deps
from sentiment_prep.api.aws_errors import translated
from sentiment_prep.config import Settings

DOWNLOAD_LINK_MEDIA_TYPE = "application/vnd.sentiment-prep.download+json"
DOWNLOAD_LINK_SECONDS = 300


class DownloadLink(BaseModel):
    """A signed URL for one private object, valid for at most five minutes."""

    url: str
    filename: str
    expires_in: int


DOWNLOAD_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "File bytes locally; a temporary private download link in Lambda.",
        "content": {DOWNLOAD_LINK_MEDIA_TYPE: {"schema": DownloadLink.model_json_schema()}},
    }
}


def download(content: bytes, media_type: str, filename: str, settings: Settings) -> Response:
    """Keep export bytes out of Lambda's synchronous response envelope."""
    if settings.runtime == "lambda":
        with translated("Amazon S3"):
            url = deps.get_s3_store().download_link(
                content, media_type, filename, expires_in=DOWNLOAD_LINK_SECONDS
            )
        link = DownloadLink(url=url, filename=filename, expires_in=DOWNLOAD_LINK_SECONDS)
        return JSONResponse(
            link.model_dump(),
            media_type=DOWNLOAD_LINK_MEDIA_TYPE,
            headers={"Cache-Control": "no-store"},
        )
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
