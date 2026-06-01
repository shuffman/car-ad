import json
import os
import re

import anthropic


async def generate_listing(
    car_info: dict,
    image_b64_list: list[str],
    pdf_b64_list: list[str] | None = None,
) -> str:
    async with anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"]) as client:
        content: list = []

        for b64 in (pdf_b64_list or [])[:5]:
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64,
                    },
                }
            )

        for b64 in image_b64_list[:4]:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                }
            )

        content.append(
            {
                "type": "text",
                "text": f"""You are a professional automotive copywriter who specializes in online car listings that generate real inquiries.

Based on the vehicle details and photos provided, write a compelling listing that stops a buyer mid-scroll and makes them pick up the phone.

Car Details:
{_format_details(car_info)}

Write the listing using this exact structure:

# [Headline]
One punchy, specific headline. Lead with the year/make/model and a key emotional hook — not generic hype.

[Opening paragraph]
2-3 sentences. Paint the picture. Who is this car for, and why will they love it?

## What Makes This One Stand Out
3-4 sentences covering what's special: condition, history, trim level, rare options, or something that creates genuine trust (one owner, garage kept, recent work, etc.).

## Key Highlights
- Bullet 1
- Bullet 2
- (5–8 bullets covering drivetrain, mileage, features, condition notes, and any extras worth calling out)

## The Bottom Line
2-3 sentences on value. Be specific about why the price is right — don't just say "priced to sell." Reference market comps, remaining warranty, or cost to replicate.

**[Call to action — one sentence urging immediate contact]**

Rules:
- Use the real data from the details; don't invent specs
- Be enthusiastic but credible — skip empty superlatives
- No clichés: "won't last", "rare find", "priced to move", "don't miss out"
- Target length: 350–450 words
- Output clean markdown only""",
            }
        )

        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1400,
            messages=[{"role": "user", "content": content}],
        )

        return message.content[0].text


async def analyze_car_photos(
    image_b64_list: list[str],
    pdf_b64_list: list[str] | None = None,
) -> dict:
    """
    Use Claude vision + document understanding to extract car details from photos and PDFs.
    Returns a dict of detected fields (only fields with confident values included).
    """
    async with anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"]) as client:
        content: list = []

        # PDFs first so Claude reads structured data (VIN, mileage, options) before the photos
        for b64 in (pdf_b64_list or [])[:5]:
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64,
                    },
                }
            )

        for b64 in image_b64_list[:6]:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                }
            )

        sources = []
        if pdf_b64_list:
            sources.append("PDF documents (build sheets, Carfax reports, window stickers, etc.)")
        if image_b64_list:
            sources.append("photos")
        source_desc = " and ".join(sources) if sources else "the provided files"

        content.append(
            {
                "type": "text",
                "text": f"""Analyze all provided {source_desc} to extract vehicle information.
PDFs may include Carfax reports (read mileage, accident history, owners), build sheets (read factory options, trim, VIN), or window stickers.
Return ONLY a valid JSON object — no explanation, no markdown fences.

Use this exact schema (set a field to null if you cannot determine it with reasonable confidence):
{{
  "year": "2020",
  "make": "BMW",
  "model": "3 Series",
  "trim": "330i xDrive",
  "vin": "WBA5R1C5XKA123456",
  "mileage": "34,217",
  "exterior_color": "Alpine White",
  "interior_color": "Black leather",
  "condition": "Excellent",
  "transmission": "Automatic",
  "drivetrain": "AWD",
  "engine": "2.0L Turbocharged 4-cylinder 255hp",
  "features": "Panoramic sunroof, 19-inch alloy wheels, LED headlights, sport seats",
  "notes": "1 owner, no accidents reported, garage-kept"
}}

Rules:
- vin: copy exactly as printed — 17 characters, no spaces
- mileage: copy the exact odometer reading from Carfax or build sheet if present
- year: 4-digit string
- condition: must be exactly one of: Excellent, Very Good, Good, Fair
- transmission: Automatic, Manual, CVT, or DCT only
- drivetrain: FWD, RWD, AWD, or 4WD / 4×4 only
- features: list features from documents or visibly in photos
- notes: include owner count, accident history, service records if found in documents
- Return ONLY the JSON object""",
            }
        )

        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": content}],
        )

        raw = message.content[0].text.strip()
        # Strip accidental markdown code fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        # Return only non-null, non-empty values
        return {k: str(v).strip() for k, v in data.items() if v not in (None, "", "null")}


def _format_details(car_info: dict) -> str:
    label_map = {
        "year": "Year",
        "make": "Make",
        "model": "Model",
        "trim": "Trim / Package",
        "vin": "VIN",
        "mileage": "Mileage",
        "price": "Asking Price",
        "exterior_color": "Exterior Color",
        "interior_color": "Interior Color",
        "condition": "Condition",
        "transmission": "Transmission",
        "drivetrain": "Drivetrain",
        "engine": "Engine",
        "features": "Features & Options",
        "notes": "Additional Notes",
    }
    lines = []
    for key, label in label_map.items():
        val = car_info.get(key, "").strip()
        if val:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines) if lines else "(No details provided)"
