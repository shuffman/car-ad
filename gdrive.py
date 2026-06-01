import os
import re
import tempfile
from pathlib import Path

import gdown

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.heif', '.heic'}
DOCUMENT_EXTENSIONS = {'.pdf'}
MAX_IMAGES = 20
MAX_DOCUMENTS = 5


def _parse_url(url: str) -> tuple[str, str]:
    """Returns (drive_id, 'folder'|'file')."""
    folder_m = re.search(r'/folders/([a-zA-Z0-9_-]+)', url)
    if folder_m:
        return folder_m.group(1), 'folder'

    file_m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if file_m:
        return file_m.group(1), 'file'

    id_m = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if id_m:
        return id_m.group(1), 'file'

    raise ValueError(
        "Could not find a Google Drive file or folder ID in that URL. "
        "Make sure you copied the full sharing link."
    )


def fetch_files_from_drive(url: str) -> tuple[list[bytes], list[bytes], str]:
    """
    Download images and PDF documents from a public Google Drive file or folder.
    Returns (image_bytes_list, pdf_bytes_list, human_readable_status).
    Raises ValueError with a user-friendly message on failure.
    """
    drive_id, drive_type = _parse_url(url)

    with tempfile.TemporaryDirectory() as tmpdir:
        if drive_type == 'folder':
            canonical = f"https://drive.google.com/drive/folders/{drive_id}"
            try:
                gdown.download_folder(
                    canonical,
                    output=tmpdir,
                    quiet=True,
                    use_cookies=False,
                    remaining_ok=True,
                )
            except Exception as e:
                raise ValueError(
                    "Could not access the Google Drive folder. "
                    "Make sure sharing is set to 'Anyone with the link can view'. "
                    f"({e})"
                )

            image_paths, doc_paths = [], []
            for root, _, files in os.walk(tmpdir):
                for fname in sorted(files):
                    ext = Path(fname).suffix.lower()
                    full = os.path.join(root, fname)
                    if ext in IMAGE_EXTENSIONS:
                        image_paths.append(full)
                    elif ext in DOCUMENT_EXTENSIONS:
                        doc_paths.append(full)

            total_images = len(image_paths)
            image_paths = image_paths[:MAX_IMAGES]
            doc_paths = doc_paths[:MAX_DOCUMENTS]

        else:
            canonical = f"https://drive.google.com/uc?id={drive_id}"
            out_path = os.path.join(tmpdir, "gdrive_file")
            try:
                gdown.download(canonical, out_path, quiet=True, fuzzy=True)
            except Exception as e:
                raise ValueError(
                    "Could not download the file from Google Drive. "
                    "Make sure sharing is set to 'Anyone with the link can view'. "
                    f"({e})"
                )
            if not os.path.exists(out_path):
                raise ValueError(
                    "The file download appeared to succeed but no file was saved."
                )

            ext = Path(out_path).suffix.lower()
            raw = Path(out_path).read_bytes()
            # For single-file links gdown doesn't preserve the extension;
            # sniff the magic bytes to determine type.
            if raw[:4] == b'%PDF':
                image_paths, doc_paths, total_images = [], [out_path], 0
            else:
                image_paths, doc_paths, total_images = [out_path], [], 1

        def _read(paths: list[str]) -> list[bytes]:
            result = []
            for p in paths:
                try:
                    data = Path(p).read_bytes()
                    if data:
                        result.append(data)
                except Exception:
                    continue
            return result

        images = _read(image_paths)
        docs = _read(doc_paths)

        if not images and not docs:
            raise ValueError(
                "No supported files were found at the Google Drive link. "
                "Supported: JPEG, PNG, WebP, BMP, TIFF images and PDF documents."
            )

        parts = []
        if images:
            parts.append(f"{len(images)} photo{'s' if len(images) != 1 else ''}")
            if total_images > MAX_IMAGES:
                parts[-1] += f" (first {MAX_IMAGES} of {total_images})"
        if docs:
            parts.append(f"{len(docs)} PDF document{'s' if len(docs) != 1 else ''}")

        return images, docs, "Downloaded " + " and ".join(parts) + " from Google Drive"
