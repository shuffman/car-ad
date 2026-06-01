import io

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

MAX_DIMENSION = 2400
ANALYSIS_DIMENSION = 1024

# brightness, contrast, color, sharpness, unsharp_percent
PRESETS: dict[str, tuple[float, float, float, float, int]] = {
    "natural":  (1.03, 1.08, 1.10, 1.25, 40),
    "standard": (1.06, 1.18, 1.22, 1.50, 65),
    "vivid":    (1.08, 1.30, 1.38, 1.65, 85),
    "bold":     (1.10, 1.42, 1.52, 1.80, 110),
}


def _normalize(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def enhance_image(image_bytes: bytes, preset: str = "standard") -> bytes:
    brightness, contrast, color, sharpness, unsharp_pct = PRESETS.get(preset, PRESETS["standard"])

    img = Image.open(io.BytesIO(image_bytes))
    img = _normalize(img)

    if max(img.size) > MAX_DIMENSION:
        ratio = MAX_DIMENSION / max(img.size)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS
        )

    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Brightness(img).enhance(brightness)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Color(img).enhance(color)
    img = ImageEnhance.Sharpness(img).enhance(sharpness)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=unsharp_pct, threshold=3))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


def resize_for_analysis(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img = _normalize(img)

    if max(img.size) > ANALYSIS_DIMENSION:
        ratio = ANALYSIS_DIMENSION / max(img.size)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS
        )

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82)
    return out.getvalue()
