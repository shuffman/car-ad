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
from image_processor import enhance_image, resize_for_analysis
from publisher import deploy_listing
from text_generator import analyze_car_photos, generate_listing

_executor = ThreadPoolExecutor(max_workers=4)
_results: dict = {}  # {uuid: {car_info, images: [bytes], listing_text}}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    _executor.shutdown(wait=False)


app = FastAPI(title="CarAd Pro", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/process")
async def process(
    request: Request,
    year: str = Form(...),
    make: str = Form(...),
    model: str = Form(...),
    trim: str = Form(""),
    vin: str = Form(""),
    mileage: str = Form(""),
    price: str = Form(""),
    exterior_color: str = Form(""),
    interior_color: str = Form(""),
    condition: str = Form(""),
    transmission: str = Form(""),
    drivetrain: str = Form(""),
    engine: str = Form(""),
    features: str = Form(""),
    notes: str = Form(""),
    gdrive_url: str = Form(""),
    images: List[UploadFile] = File(default=[]),
):
    car_info = {
        "year": year,
        "make": make,
        "model": model,
        "trim": trim,
        "vin": vin,
        "mileage": mileage,
        "price": price,
        "exterior_color": exterior_color,
        "interior_color": interior_color,
        "condition": condition,
        "transmission": transmission,
        "drivetrain": drivetrain,
        "engine": engine,
        "features": features,
        "notes": notes,
    }

    loop = asyncio.get_event_loop()
    drive_error: Optional[str] = None

    async def _process_upload(upload: UploadFile) -> tuple[str, Optional[bytes]]:
        """Returns ('image'|'pdf'|'skip', bytes|None)."""
        if not upload.filename:
            return 'skip', None
        try:
            raw = await upload.read()
            if not raw:
                return 'skip', None
            ext = upload.filename.rsplit('.', 1)[-1].lower()
            if ext == 'pdf' or raw[:4] == b'%PDF':
                return 'pdf', raw
            return 'image', await loop.run_in_executor(_executor, enhance_image, raw)
        except Exception:
            return 'skip', None

    async def _fetch_drive() -> tuple[list[bytes], list[bytes]]:
        nonlocal drive_error
        if not gdrive_url.strip():
            return [], []
        try:
            imgs, docs, _ = await fetch_files_from_drive(gdrive_url.strip())
        except ValueError as e:
            drive_error = str(e)
            return [], []
        enhance_tasks = [loop.run_in_executor(_executor, enhance_image, r) for r in imgs]
        enhanced = await asyncio.gather(*enhance_tasks, return_exceptions=True)
        return [r for r in enhanced if isinstance(r, bytes)], docs

    upload_coros = [_process_upload(img) for img in images]
    all_results = await asyncio.gather(*upload_coros, _fetch_drive())

    enhanced_images: list[bytes] = []
    uploaded_pdfs: list[bytes] = []
    for kind, data in all_results[:-1]:
        if kind == 'image' and data:
            enhanced_images.append(data)
        elif kind == 'pdf' and data:
            uploaded_pdfs.append(data)

    drive_images, drive_pdfs = all_results[-1]
    enhanced_images.extend(drive_images)
    all_pdfs = uploaded_pdfs + drive_pdfs

    img_b64 = [base64.b64encode(img).decode() for img in enhanced_images[:4]]
    pdf_b64 = [base64.b64encode(doc).decode() for doc in all_pdfs[:5]]

    try:
        listing_text = await generate_listing(car_info, img_b64, pdf_b64 or None)
    except Exception as e:
        listing_text = f"*(Listing generation failed: {e}. Please check your ANTHROPIC_API_KEY.)*"

    result_id = str(uuid.uuid4())
    _results[result_id] = {
        "car_info": car_info,
        "images": enhanced_images,
        "pdfs": all_pdfs,
        "listing_text": listing_text,
        "drive_error": drive_error,
    }

    return RedirectResponse(url=f"/result/{result_id}", status_code=303)


@app.post("/analyze")
async def analyze(
    images: List[UploadFile] = File(default=[]),
    gdrive_url: str = Form(""),
):
    loop = asyncio.get_event_loop()
    raw_images: list[bytes] = []
    raw_pdfs: list[bytes] = []

    for upload in images:
        if not upload.filename:
            continue
        raw = await upload.read()
        if not raw:
            continue
        if raw[:4] == b'%PDF' or upload.filename.lower().endswith('.pdf'):
            raw_pdfs.append(raw)
        else:
            raw_images.append(raw)

    # Supplement with Drive files if a URL was provided
    if gdrive_url.strip():
        try:
            drive_imgs, drive_docs, _ = await fetch_files_from_drive(gdrive_url.strip())
            raw_images.extend(drive_imgs)
            raw_pdfs.extend(drive_docs)
        except ValueError as e:
            if not raw_images and not raw_pdfs:
                return JSONResponse({"error": str(e)}, status_code=400)

    if not raw_images and not raw_pdfs:
        return JSONResponse({"error": "No images or documents provided."}, status_code=400)

    # Resize images for fast analysis; PDFs go straight to Claude as-is
    resize_tasks = [
        loop.run_in_executor(_executor, resize_for_analysis, raw)
        for raw in raw_images[:6]
    ]
    resized = await asyncio.gather(*resize_tasks, return_exceptions=True)
    img_b64 = [base64.b64encode(r).decode() for r in resized if isinstance(r, bytes)]
    pdf_b64 = [base64.b64encode(r).decode() for r in raw_pdfs[:5]]

    try:
        detected = await analyze_car_photos(img_b64, pdf_b64 or None)
        return JSONResponse(detected)
    except Exception as e:
        return JSONResponse({"error": f"Analysis failed: {e}"}, status_code=500)


@app.post("/deploy/{result_id}")
async def deploy(result_id: str):
    data = _results.get(result_id)
    if not data:
        return JSONResponse({"error": "Result not found or expired."}, status_code=404)
    try:
        url = await deploy_listing(
            car_info=data["car_info"],
            enhanced_images=data["images"],
            listing_text=data["listing_text"],
        )
        return JSONResponse({"url": url})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Deploy failed: {e}"}, status_code=500)


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
            "car_info": data["car_info"],
            "image_count": len(data["images"]),
            "listing_text": data["listing_text"],
            "drive_error": data.get("drive_error"),
        },
    )


@app.get("/image/{result_id}/{index}")
async def serve_image(result_id: str, index: int):
    data = _results.get(result_id)
    if not data or index < 0 or index >= len(data["images"]):
        raise HTTPException(status_code=404)
    return StreamingResponse(
        io.BytesIO(data["images"][index]),
        media_type="image/jpeg",
        headers={"Cache-Control": "max-age=3600"},
    )


@app.get("/download/{result_id}/{index}")
async def download_image(result_id: str, index: int):
    data = _results.get(result_id)
    if not data or index < 0 or index >= len(data["images"]):
        raise HTTPException(status_code=404)
    return StreamingResponse(
        io.BytesIO(data["images"][index]),
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="enhanced-photo-{index + 1}.jpg"'
        },
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
