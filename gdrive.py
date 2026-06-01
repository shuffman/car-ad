"""
Google Drive file fetcher using the Drive API v3.
Requires GOOGLE_DRIVE_API_KEY env var — see setup instructions below.

Setup (one-time, ~5 min):
  1. Go to https://console.cloud.google.com and create a project
  2. Enable the "Google Drive API" in APIs & Services → Library
  3. Go to APIs & Services → Credentials → Create Credentials → API key
  4. (Optional but recommended) Restrict the key to the Drive API
  5. railway variables --set "GOOGLE_DRIVE_API_KEY=AIza..."
"""

import asyncio
import os
import re

import httpx

DRIVE_API = "https://www.googleapis.com/drive/v3"

IMAGE_MIMETYPES = {
    "image/jpeg", "image/png", "image/webp",
    "image/bmp", "image/tiff", "image/heic",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".heif", ".heic"}
MAX_IMAGES = 20
MAX_DOCUMENTS = 5


def _parse_url(url: str) -> tuple[str, str]:
    """Returns (drive_id, 'folder'|'file')."""
    folder_m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if folder_m:
        return folder_m.group(1), "folder"

    file_m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if file_m:
        return file_m.group(1), "file"

    id_m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if id_m:
        return id_m.group(1), "file"

    raise ValueError(
        "Could not find a Google Drive file or folder ID in that URL. "
        "Make sure you copied the full sharing link."
    )


def _api_key() -> str:
    key = os.environ.get("GOOGLE_DRIVE_API_KEY", "")
    if not key:
        raise ValueError(
            "GOOGLE_DRIVE_API_KEY is not set. "
            "Quick setup: console.cloud.google.com → new project → "
            "enable Drive API → Credentials → Create API key. "
            "Then: railway variables --set \"GOOGLE_DRIVE_API_KEY=AIza...\""
        )
    return key


async def fetch_files_from_drive(url: str) -> tuple[list[bytes], list[bytes], str]:
    """
    Download images and PDF documents from a public Google Drive file or folder.
    Returns (image_bytes_list, pdf_bytes_list, human_readable_status).
    Raises ValueError with a user-friendly message on failure.
    """
    key = _api_key()
    drive_id, drive_type = _parse_url(url)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        if drive_type == "folder":
            return await _fetch_folder(client, drive_id, key)
        else:
            return await _fetch_single_file(client, drive_id, key)


async def _fetch_folder(
    client: httpx.AsyncClient, folder_id: str, key: str
) -> tuple[list[bytes], list[bytes], str]:
    r = await client.get(
        f"{DRIVE_API}/files",
        params={
            "q": f"'{folder_id}' in parents and trashed=false",
            "key": key,
            "fields": "files(id,name,mimeType)",
            "pageSize": 100,
            "orderBy": "name",
        },
    )
    _check_response(r, "folder")

    files = r.json().get("files", [])
    if not files:
        raise ValueError(
            "No files found in that Google Drive folder. "
            "Make sure the folder is shared as 'Anyone with the link can view'."
        )

    image_ids, pdf_ids = [], []
    for f in files:
        mime = f.get("mimeType", "")
        name = f.get("name", "").lower()
        fid = f["id"]
        if mime in IMAGE_MIMETYPES or any(name.endswith(e) for e in IMAGE_EXTENSIONS):
            image_ids.append(fid)
        elif mime == "application/pdf" or name.endswith(".pdf"):
            pdf_ids.append(fid)

    image_ids = image_ids[:MAX_IMAGES]
    pdf_ids = pdf_ids[:MAX_DOCUMENTS]

    img_results, pdf_results = await asyncio.gather(
        asyncio.gather(*[_download(client, fid, key) for fid in image_ids], return_exceptions=True),
        asyncio.gather(*[_download(client, fid, key) for fid in pdf_ids], return_exceptions=True),
    )

    images = [r for r in img_results if isinstance(r, bytes) and r]
    pdfs = [r for r in pdf_results if isinstance(r, bytes) and r]

    if not images and not pdfs:
        raise ValueError(
            "No supported files could be downloaded from the folder. "
            "Supported: JPEG, PNG, WebP, BMP, TIFF images and PDF documents."
        )

    parts = []
    if images:
        parts.append(f"{len(images)} photo{'s' if len(images) != 1 else ''}")
    if pdfs:
        parts.append(f"{len(pdfs)} PDF{'s' if len(pdfs) != 1 else ''}")
    return images, pdfs, "Downloaded " + " and ".join(parts) + " from Google Drive"


async def _fetch_single_file(
    client: httpx.AsyncClient, file_id: str, key: str
) -> tuple[list[bytes], list[bytes], str]:
    r = await client.get(
        f"{DRIVE_API}/files/{file_id}",
        params={"key": key, "fields": "name,mimeType"},
    )
    _check_response(r, "file")
    info = r.json()

    data = await _download(client, file_id, key)
    if not data:
        raise ValueError("Could not download the file from Google Drive.")

    mime = info.get("mimeType", "")
    name = info.get("name", "").lower()

    if mime == "application/pdf" or name.endswith(".pdf"):
        return [], [data], "Downloaded 1 PDF from Google Drive"
    return [data], [], "Downloaded 1 photo from Google Drive"


async def _download(client: httpx.AsyncClient, file_id: str, key: str) -> bytes | None:
    try:
        r = await client.get(
            f"{DRIVE_API}/files/{file_id}",
            params={"alt": "media", "key": key},
        )
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


def _check_response(r: httpx.Response, kind: str) -> None:
    if r.status_code == 403:
        raise ValueError(
            f"Access denied to that Google Drive {kind}. "
            "Make sure it is shared as 'Anyone with the link can view', "
            "and that your GOOGLE_DRIVE_API_KEY has the Drive API enabled."
        )
    if r.status_code == 404:
        raise ValueError(
            f"Google Drive {kind} not found. Check that the link is correct."
        )
    r.raise_for_status()
