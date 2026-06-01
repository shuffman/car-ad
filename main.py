import asyncio
import base64
import io
import json
import os
import uuid
import zipfile
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
from text_generator import analyze_car_photos, generate_listing, refine_listing

_executor = ThreadPoolExecutor(max_workers=4)
_results: dict = {}
_jobs: dict = {}   # job_id → {step, detail, progress, result_id, error, drive_error}


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


# ── Process: read uploads, start background job, return job_id ─────────────────

@app.post("/process")
async def process(
    images: List[UploadFile] = File(default=[]),
    gdrive_url: str = Form(""),
    extra_info: str = Form(""),
):
    raw_images: list[bytes] = []
    raw_pdfs: list[bytes] = []

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

    # Need at least something to work with (uploaded files or a Drive URL)
    if not raw_images and not raw_pdfs and not gdrive_url.strip():
        return JSONResponse({"error": "no_files"}, status_code=400)

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "step": "queued",
        "detail": "Starting…",
        "progress": 0,
        "result_id": None,
        "error": None,
        "drive_error": None,
    }

    asyncio.create_task(
        _run_job(job_id, raw_images, raw_pdfs, gdrive_url.strip(), extra_info.strip())
    )

    return JSONResponse({"job_id": job_id})


# ── Background job ─────────────────────────────────────────────────────────────

async def _run_job(
    job_id: str,
    raw_images: list[bytes],
    raw_pdfs: list[bytes],
    gdrive_url: str,
    extra_info: str,
) -> None:
    job = _jobs[job_id]
    loop = asyncio.get_event_loop()

    def status(step: str, detail: str, progress: int) -> None:
        job.update({"step": step, "detail": detail, "progress": progress})

    try:
        # ── 1. Download from Drive ──────────────────────────────────────────
        if gdrive_url:
            status("downloading", "Connecting to Google Drive…", 2)

            def on_progress(current: int, total: int) -> None:
                pct = int(current / total * 38)
                status(
                    "downloading",
                    f"Downloading {current} of {total} from Google Drive…",
                    2 + pct,
                )

            try:
                drive_imgs, drive_docs, _ = await fetch_files_from_drive(
                    gdrive_url, on_progress=on_progress
                )
                raw_images.extend(drive_imgs)
                raw_pdfs.extend(drive_docs)
            except ValueError as e:
                job["drive_error"] = str(e)

        if not raw_images and not raw_pdfs:
            job.update({
                "step": "error",
                "error": "No images or documents found. Check your Drive link or upload files directly.",
                "progress": 0,
            })
            return

        # ── 2. Enhance images ───────────────────────────────────────────────
        n = len(raw_images)
        status("enhancing", f"Enhancing {n} photo{'s' if n != 1 else ''}…", 42)

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

        # ── 3. Analyze ──────────────────────────────────────────────────────
        status("analyzing", "Analyzing vehicle…", 62)

        img_b64_small = [base64.b64encode(r).decode() for r in resized_images]
        pdf_b64 = [base64.b64encode(r).decode() for r in raw_pdfs[:5]]

        try:
            car_info = await analyze_car_photos(img_b64_small, pdf_b64 or None)
        except Exception:
            car_info = {}

        # ── 4. Generate listing ─────────────────────────────────────────────
        status("generating", "Writing your listing…", 78)

        img_b64_full = [base64.b64encode(r).decode() for r in enhanced_images[:4]]
        try:
            listing_text = await generate_listing(
                car_info, img_b64_full, pdf_b64 or None, extra_info or None
            )
        except Exception as e:
            listing_text = f"*(Could not generate listing: {e})*"

        # ── 5. Store & finish ───────────────────────────────────────────────
        result_id = str(uuid.uuid4())
        _results[result_id] = {
            "car_info": car_info,
            "extra_info": extra_info,
            "raw_images": raw_images,
            "images": enhanced_images,
            "pdfs": raw_pdfs,
            "listing_text": listing_text,
            "enhance_preset": "standard",
            "drive_error": job.get("drive_error"),
        }

        job.update({"step": "done", "detail": "Done!", "progress": 100, "result_id": result_id})

    except Exception as e:
        job.update({"step": "error", "error": str(e), "progress": 0})


# ── SSE progress stream ────────────────────────────────────────────────────────

@app.get("/progress/{job_id}")
async def progress_stream(job_id: str):
    async def generate():
        while True:
            job = _jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'step': 'error', 'error': 'Job not found'})}\n\n"
                return
            yield f"data: {json.dumps(job)}\n\n"
            if job["step"] in ("done", "error"):
                return
            await asyncio.sleep(0.35)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Regenerate listing text ────────────────────────────────────────────────────

@app.post("/regenerate/{result_id}")
async def regenerate(result_id: str, request: Request):
    data = _results.get(result_id)
    if not data:
        return JSONResponse({"error": "Result not found."}, status_code=404)

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    # Prefer notes sent from the client (latest from the textarea),
    # fall back to what was submitted with the original form.
    extra_info = body.get("extra_info", "").strip() or data.get("extra_info") or None

    img_b64 = [base64.b64encode(img).decode() for img in data["images"][:4]]
    pdf_b64 = [base64.b64encode(d).decode() for d in data.get("pdfs", [])[:5]]

    try:
        listing_text = await generate_listing(
            data["car_info"], img_b64, pdf_b64 or None, extra_info,
        )
        data["listing_text"] = listing_text
        return JSONResponse({"listing_text": listing_text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Refine listing with corrections ───────────────────────────────────────────

@app.post("/refine/{result_id}")
async def refine(result_id: str, request: Request):
    data = _results.get(result_id)
    if not data:
        return JSONResponse({"error": "Result not found."}, status_code=404)

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    correction = body.get("correction", "").strip()
    current_text = body.get("current_text", "").strip() or data["listing_text"]

    if not correction:
        return JSONResponse({"error": "No correction provided."}, status_code=400)

    try:
        refined = await refine_listing(current_text, correction)
        data["listing_text"] = refined
        return JSONResponse({"listing_text": refined})
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


# ── Download zip ──────────────────────────────────────────────────────────────

@app.post("/download-zip/{result_id}")
async def download_zip(result_id: str, request: Request):
    data = _results.get(result_id)
    if not data:
        raise HTTPException(status_code=404)

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    listing_text = body.get("listing_text") or data["listing_text"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, img_bytes in enumerate(data["images"]):
            zf.writestr(f"photos/photo-{i + 1}.jpg", img_bytes)
        zf.writestr("listing.txt", listing_text)
    buf.seek(0)

    car = data.get("car_info", {})
    name = " ".join(p for p in [car.get("year"), car.get("make"), car.get("model")] if p)
    filename = (name.lower().replace(" ", "-") or "listing") + ".zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
