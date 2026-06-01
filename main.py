import asyncio
import base64
import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()

from gdrive import fetch_files_from_drive
from image_processor import PRESETS, enhance_image, resize_for_analysis
from publisher import deploy_listing
from text_generator import analyze_car_photos, generate_listing

_executor = ThreadPoolExecutor(max_workers=4)
_results: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    _executor.shutdown(wait=False)


app = FastAPI(title="CarAd Pro", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    error = request.query_params.get("error", "")
    return templates.TemplateResponse("index.html", {"request": request, "error": error})


@app.get("/result/{result_id}", response_class=HTMLResponse)
async def result_page(request: Request, result_id: str):
    data = _results.get(result_id)
    if not data:
        raise HTTPException(status_code=404, detail="Result not found or expired.")
    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "result_id": result_id,
            "image_count": len(data["images"]),
            "listing_text": data["listing_text"],
            "enhance_preset": data.get("enhance_preset", "standard"),
            "drive_error": data.get("drive_error"),
        },
    )


# ── Process ────────────────────────────────────────────────────────────────────

@app.post("/process")
async def process(
    images: List[UploadFile] = File(default=[]),
    gdrive_url: str = Form(""),
):
    loop = asyncio.get_event_loop()
    raw_images: list[bytes] = []
    raw_pdfs: list[bytes] = []
    drive_error: Optional[str] = None

    for upload in images:
        if not upload.filename:
            continue
        raw = await upload.read()
        if not raw:
            continue
        if raw[:4] == b"%PDF" or upload.filename.lower().endswith(".pdf"):
            raw_pdfs.append(raw)
        else:
            raw_images.append(raw)

    if gdrive_url.strip():
        try:
            drive_imgs, drive_docs, _ = await fetch_files_from_drive(gdrive_url.strip())
            raw_images.extend(drive_imgs)
            raw_pdfs.extend(drive_docs)
        except ValueError as e:
            drive_error = str(e)

    if not raw_images and not raw_pdfs:
        return RedirectResponse(url="/?error=no_files", status_code=303)

    # Concurrently enhance all images and resize a subset for analysis
    enhanced_results, resized_results = await asyncio.gather(
        asyncio.gather(
            *[loop.run_in_executor(_executor, enhance_image, r) for r in raw_images],
            return_exceptions=True,
        ),
        asyncio.gather(
            *[loop.run_in_executor(_executor, resize_for_analysis, r) for r in raw_images[:6]],
            return_exceptions=True,
        ),
    )

    enhanced_images = [r for r in enhanced_results if isinstance(r, bytes)]
    resized_images = [r for r in resized_results if isinstance(r, bytes)]

    img_b64_small = [base64.b64encode(r).decode() for r in resized_images]
    pdf_b64 = [base64.b64encode(r).decode() for r in raw_pdfs[:5]]

    try:
        car_info = await analyze_car_photos(img_b64_small, pdf_b64 or None)
    except Exception:
        car_info = {}

    img_b64_full = [base64.b64encode(r).decode() for r in enhanced_images[:4]]
    try:
        listing_text = await generate_listing(car_info, img_b64_full, pdf_b64 or None)
    except Exception as e:
        listing_text = f"*(Could not generate listing: {e})*"

    result_id = str(uuid.uuid4())
    _results[result_id] = {
        "car_info": car_info,
        "raw_images": raw_images,
        "images": enhanced_images,
        "pdfs": raw_pdfs,
        "listing_text": listing_text,
        "enhance_preset": "standard",
        "drive_error": drive_error,
    }

    return RedirectResponse(url=f"/result/{result_id}", status_code=303)


# ── Regenerate listing text ────────────────────────────────────────────────────

@app.post("/regenerate/{result_id}")
async def regenerate(result_id: str):
    data = _results.get(result_id)
    if not data:
        return JSONResponse({"error": "Result not found."}, status_code=404)

    img_b64 = [base64.b64encode(img).decode() for img in data["images"][:4]]
    pdf_b64 = [base64.b64encode(d).decode() for d in data.get("pdfs", [])[:5]]

    try:
        listing_text = await generate_listing(
            data["car_info"], img_b64, pdf_b64 or None
        )
        data["listing_text"] = listing_text
        return JSONResponse({"listing_text": listing_text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Re-enhance with preset ─────────────────────────────────────────────────────

@app.post("/enhance/{result_id}/{preset}")
async def enhance_preset(result_id: str, preset: str):
    data = _results.get(result_id)
    if not data:
        return JSONResponse({"error": "Result not found."}, status_code=404)
    if preset not in PRESETS:
        return JSONResponse({"error": "Unknown preset."}, status_code=400)

    raw_images = data.get("raw_images", [])
    if not raw_images:
        return JSONResponse({"error": "Original images unavailable."}, status_code=400)

    loop = asyncio.get_event_loop()
    results = await asyncio.gather(
        *[loop.run_in_executor(_executor, enhance_image, r, preset) for r in raw_images],
        return_exceptions=True,
    )
    data["images"] = [r for r in results if isinstance(r, bytes)]
    data["enhance_preset"] = preset
    return JSONResponse({"ok": True})


# ── Serve images ───────────────────────────────────────────────────────────────

@app.get("/image/{result_id}/{index}")
async def serve_image(result_id: str, index: int):
    data = _results.get(result_id)
    if not data or index < 0 or index >= len(data["images"]):
        raise HTTPException(status_code=404)
    return StreamingResponse(
        io.BytesIO(data["images"][index]),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/download/{result_id}/{index}")
async def download_image(result_id: str, index: int):
    data = _results.get(result_id)
    if not data or index < 0 or index >= len(data["images"]):
        raise HTTPException(status_code=404)
    return StreamingResponse(
        io.BytesIO(data["images"][index]),
        media_type="image/jpeg",
        headers={"Content-Disposition": f'attachment; filename="photo-{index + 1}.jpg"'},
    )


# ── Deploy to forsale ──────────────────────────────────────────────────────────

@app.post("/deploy/{result_id}")
async def deploy(result_id: str, request: Request):
    data = _results.get(result_id)
    if not data:
        return JSONResponse({"error": "Result not found or expired."}, status_code=404)

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    listing_html = body.get("listing_html")
    listing_text = body.get("listing_text") or data["listing_text"]

    try:
        url = await deploy_listing(
            car_info=data["car_info"],
            enhanced_images=data["images"],
            listing_text=listing_text,
            listing_html=listing_html,
        )
        return JSONResponse({"url": url})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Deploy failed: {e}"}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
