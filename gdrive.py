import os
import re
import tempfile
from pathlib import Path

import gdown

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.heif', '.heic'}
MAX_IMAGES = 20


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


def fetch_images_from_drive(url: str) -> tuple[list[bytes], str]:
    """
    Download images from a public Google Drive file or folder link.
    Returns (image_bytes_list, human_readable_status).
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

            image_paths = []
            for root, _, files in os.walk(tmpdir):
                for fname in sorted(files):
                    if Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                        image_paths.append(os.path.join(root, fname))

            total_found = len(image_paths)
            image_paths = image_paths[:MAX_IMAGES]

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
                    "The file download appeared to succeed but no file was saved. "
                    "The link may point to a page rather than an image."
                )
            image_paths = [out_path]
            total_found = 1

        image_bytes: list[bytes] = []
        for path in image_paths:
            try:
                data = Path(path).read_bytes()
                if data:
                    image_bytes.append(data)
            except Exception:
                continue

        if not image_bytes:
            raise ValueError(
                "No supported image files were found at the Google Drive link. "
                "Supported formats: JPEG, PNG, WebP, BMP, TIFF."
            )

        count = len(image_bytes)
        status = f"Downloaded {count} photo{'s' if count != 1 else ''} from Google Drive"
        if total_found > MAX_IMAGES:
            status += f" (first {MAX_IMAGES} of {total_found})"

        return image_bytes, status
