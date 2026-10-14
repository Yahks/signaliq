"""Storage helper for user-uploaded images.

Currently writes to apps/api/static/uploads/{uuid}.{ext}, which the
API serves under /static/uploads/. This is the same pattern as
chart.py's /static/charts/.

If the deployment grows to multiple API instances, swap the body of
save_upload() and delete_upload() to write to MinIO (already in the
compose stack). The signature stays the same.

File naming: random UUID, not the user's filename. Prevents path
traversal and collisions. The original extension is preserved so
browsers can sniff the MIME type.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from packages.core.logging import get_logger

log = get_logger(__name__)


# Same root the API mounts at /static.
_UPLOAD_DIR = Path("apps/api/static/uploads")

# Allowlist of image extensions. Anything else is rejected.
_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Per-file size cap. 8 MB covers any chart screenshot; larger
# suggests the user attached the wrong file.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def save_upload(data: bytes, original_filename: str | None) -> str:
    """Persist an uploaded image. Returns the URL path.

    "/static/uploads/<uuid>.<ext>"

    Raises ValueError on oversized input or disallowed extension.
    """
    if not data:
        raise ValueError("empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"upload too large: {len(data)} bytes (max {MAX_UPLOAD_BYTES})"
        )

    ext = _safe_extension(original_filename)
    if ext not in _ALLOWED_EXTS:
        raise ValueError(f"unsupported image extension: {ext!r}")

    name = f"{uuid.uuid4().hex}{ext}"
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = _UPLOAD_DIR / name
    target.write_bytes(data)

    log.info(
        "upload.saved",
        file=name,
        bytes=len(data),
        ext=ext,
    )
    return f"/static/uploads/{name}"


def delete_upload(url_path: str) -> bool:
    """Remove a previously uploaded file. Returns True on success.

    Does not raise on missing files — deletion is idempotent.
    """
    if not url_path or not url_path.startswith("/static/uploads/"):
        return False
    name = url_path.rsplit("/", 1)[-1]
    # Guard against traversal via the URL path.
    if "/" in name or ".." in name:
        return False
    target = _UPLOAD_DIR / name
    if not target.exists():
        return False
    try:
        target.unlink()
        log.info("upload.deleted", file=name)
        return True
    except OSError as exc:
        log.warning("upload.delete_failed", file=name, error=str(exc)[:200])
        return False


def _safe_extension(filename: str | None) -> str:
    """Return the lowercase extension from a filename, or a default."""
    if not filename:
        return ".png"
    p = Path(filename)
    return p.suffix.lower() or ".png"
