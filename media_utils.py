"""Small, dependency-light media inspection helpers.

The web and MCP upload paths both store immutable originals.  This module only
validates the uploaded file and creates a JPEG preview; it deliberately keeps
the optional video support behind the ffmpeg command line tools so the image
only installation remains usable when ffmpeg is not installed.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


IMAGE_FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}
VIDEO_EXTENSIONS = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".wmv": "video/x-ms-wmv",
}


def _mime_for_path(path: Path, declared_mime: str | None = None) -> str:
    if declared_mime and declared_mime != "application/octet-stream":
        return declared_mime.split(";", 1)[0].strip().lower()
    return (mimetypes.guess_type(path.name)[0] or "").lower()


def is_video_filename(filename: str, declared_mime: str | None = None) -> bool:
    mime = _mime_for_path(Path(filename), declared_mime)
    return mime.startswith("video/") or Path(filename).suffix.lower() in VIDEO_EXTENSIONS


def _probe_video(path: Path) -> tuple[int, int]:
    """Return the first video stream dimensions when ffprobe is available."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return (0, 0)
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
            capture_output=True, text=True, timeout=20, check=False,
        )
        value = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        width, height = (int(item) for item in value.split("x", 1))
        return (max(0, width), max(0, height))
    except (OSError, ValueError, subprocess.SubprocessError):
        return (0, 0)


def _video_thumbnail(path: Path, output: Path) -> tuple[int, int, bool]:
    """Extract a first-frame JPEG, falling back to a neutral placeholder."""
    ffmpeg = shutil.which("ffmpeg")
    dimensions = _probe_video(path)
    if ffmpeg:
        try:
            # Use a bounded thumbnail to avoid creating a huge preview while
            # preserving the first frame and its aspect ratio.
            result = subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-i", str(path), "-frames:v", "1",
                 "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
                 "-q:v", "3", str(output)],
                capture_output=True, timeout=60, check=False,
            )
            if result.returncode == 0 and output.exists() and output.stat().st_size:
                return (*dimensions, True)
        except (OSError, subprocess.SubprocessError):
            return (*dimensions, False)
        return (*dimensions, False)
    # A video can still be indexed and played when a host does not include
    # ffmpeg.  Keep the API contract of always returning a preview URL.
    fallback_width, fallback_height = dimensions or (640, 360)
    if fallback_width <= 0 or fallback_height <= 0:
        fallback_width, fallback_height = (640, 360)
    Image.new("RGB", (min(640, fallback_width), min(640, fallback_height)), "#222222").save(output, "JPEG", quality=82)
    return (*dimensions, True)


def inspect_media(path: str | Path, filename: str, declared_mime: str | None, thumbnail_path: str | Path) -> dict:
    """Validate one media file and write its JPEG first-frame thumbnail.

    Returns ``kind`` (``image`` or ``video``), canonical MIME, dimensions and
    the file extension used for the immutable original.  Callers should map
    ``ValueError`` to their API's validation response.
    """
    source = Path(path)
    thumbnail = Path(thumbnail_path)
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    mime_hint = _mime_for_path(Path(filename), declared_mime)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                fmt = image.format
                if fmt not in IMAGE_FORMATS:
                    raise ValueError("Unsupported image format")
                image.verify()
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                dimensions = image.size
                image.thumbnail((640, 640))
                if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    bg = Image.new("RGB", rgba.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.split()[3])
                    thumb = bg
                else:
                    thumb = image.convert("RGB")
                thumb.save(thumbnail, "JPEG", quality=85)
        ext, mime = IMAGE_FORMATS[fmt]
        return {"kind": "image", "mime": mime, "width": dimensions[0], "height": dimensions[1], "ext": ext}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, ValueError) as image_error:
        if not (mime_hint.startswith("video/") or suffix in VIDEO_EXTENSIONS):
            raise ValueError("Invalid image or unsupported media format") from image_error
    width, height, valid = _video_thumbnail(source, thumbnail)
    if not valid:
        raise ValueError("Invalid video")
    mime = mime_hint if mime_hint.startswith("video/") else VIDEO_EXTENSIONS.get(suffix, "video/mp4")
    ext = suffix.lstrip(".") or "mp4"
    return {"kind": "video", "mime": mime, "width": width, "height": height, "ext": ext}


def clean_relative_path(filename: str) -> tuple[str, str]:
    """Return safe basename and preserved browser folder path."""
    raw = str(filename or "").replace("\\", "/").replace("\x00", "")
    parts = [part for part in raw.split("/") if part not in ("", ".", "..")]
    if not parts:
        return ("素材", "")
    name = parts[-1].replace("\r", "").replace("\n", "")[:255] or "素材"
    relative = "/".join(parts[:-1] + [name])[:1000]
    return (name, relative)
