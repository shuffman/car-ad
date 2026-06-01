"""
Push a car listing to the shuffman/forsale GitHub Pages repo via the Contents API.
"""

import base64
import os
import re
from datetime import datetime
from urllib.parse import quote

import httpx

GITHUB_API = "https://api.github.com"
FORSALE_REPO = "shuffman/forsale"
PAGES_BASE = "https://shuffman.github.io/forsale"
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "shuffman@gmail.com")


# ── GitHub helpers ─────────────────────────────────────────────────────────────

def _gh_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ValueError(
            "GITHUB_TOKEN is not set. "
            "Run: railway variables --set \"GITHUB_TOKEN=ghp_...\" "
            "then redeploy."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get_file(client: httpx.AsyncClient, path: str) -> tuple[str | None, str | None]:
    """Returns (content_str, sha) or (None, None) if not found."""
    r = await client.get(f"{GITHUB_API}/repos/{FORSALE_REPO}/contents/{path}")
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


async def _put_file(
    client: httpx.AsyncClient,
    path: str,
    content: bytes,
    message: str,
) -> None:
    """Create or update a file, fetching its current SHA automatically."""
    r = await client.get(f"{GITHUB_API}/repos/{FORSALE_REPO}/contents/{path}")
    sha = r.json().get("sha") if r.status_code == 200 else None

    body: dict = {"message": message, "content": base64.b64encode(content).decode()}
    if sha:
        body["sha"] = sha

    r = await client.put(
        f"{GITHUB_API}/repos/{FORSALE_REPO}/contents/{path}", json=body
    )
    r.raise_for_status()


# ── Entry point ────────────────────────────────────────────────────────────────

async def deploy_listing(
    car_info: dict,
    enhanced_images: list[bytes],
    listing_text: str,
) -> str:
    """
    Write images + listing page + update index to the forsale repo.
    Returns the public GitHub Pages URL for the new listing.
    """
    headers = _gh_headers()
    slug = _make_slug(car_info)

    async with httpx.AsyncClient(headers=headers, timeout=120) as client:
        # 1. Upload images
        for i, img_bytes in enumerate(enhanced_images):
            fname = "hero.jpg" if i == 0 else f"photo-{i}.jpg"
            await _put_file(
                client,
                f"items/{slug}/images/{fname}",
                img_bytes,
                f"carad: add {slug} image {i + 1}",
            )

        # 2. Create/update listing page
        page_html = _render_listing_page(slug, car_info, listing_text, len(enhanced_images))
        await _put_file(
            client,
            f"items/{slug}/index.html",
            page_html.encode(),
            f"carad: add {slug} listing",
        )

        # 3. Add card to root index.html
        await _update_root_index(client, slug, car_info, listing_text)

    return f"{PAGES_BASE}/items/{slug}/"


# ── Root index update ──────────────────────────────────────────────────────────

async def _update_root_index(
    client: httpx.AsyncClient,
    slug: str,
    car_info: dict,
    listing_text: str,
) -> None:
    current, sha = await _get_file(client, "index.html")
    if current is None or sha is None:
        return

    # Don't add duplicate cards
    if f'href="items/{slug}/"' in current:
        return

    card = _render_index_card(slug, car_info, listing_text)
    marker = "<!-- Copy the block above to add more listings -->"
    if marker in current:
        updated = current.replace(marker, f"{card}\n\n      {marker}")
    else:
        updated = current.replace(
            "</div>\n  </main>",
            f"      {card}\n\n    </div>\n  </main>",
        )

    await _put_file(client, "index.html", updated.encode(), f"carad: add {slug} to index")


def _render_index_card(slug: str, car_info: dict, listing_text: str) -> str:
    title = _title(car_info)
    price = f"${car_info['price']}" if car_info.get("price") else ""
    badge = f'<span class="card-badge">{price}</span>' if price else ""

    sub_parts = [p for p in [
        car_info.get("mileage"),
        car_info.get("exterior_color"),
        car_info.get("transmission"),
    ] if p]
    subtitle = " &middot; ".join(sub_parts)
    excerpt = _first_paragraph(listing_text)

    return (
        f'<a href="items/{slug}/" class="listing-card">\n'
        f'        <div class="card-image">\n'
        f'          <img src="items/{slug}/images/hero.jpg" alt="{_esc(title)}">\n'
        f'          {badge}\n'
        f'        </div>\n'
        f'        <div class="card-body">\n'
        f'          <h2 class="card-title">{_esc(title)}</h2>\n'
        f'          <p class="card-subtitle">{subtitle}</p>\n'
        f'          <p class="card-excerpt">{_esc(excerpt)}</p>\n'
        f'        </div>\n'
        f'      </a>'
    )


# ── Listing page HTML ──────────────────────────────────────────────────────────

def _render_listing_page(
    slug: str,
    car_info: dict,
    listing_text: str,
    n_images: int,
) -> str:
    title = _title(car_info)
    price_str = f"${car_info['price']}" if car_info.get("price") else "Contact for price"
    month_year = datetime.now().strftime("%B %Y")

    # Gallery
    gallery_main = f'<img id="gallery-hero" src="images/hero.jpg" alt="{_esc(title)}">'
    thumbs_html = ""
    for i in range(n_images):
        fname = "hero.jpg" if i == 0 else f"photo-{i}.jpg"
        active = " active" if i == 0 else ""
        thumbs_html += (
            f'      <button class="thumb{active}" onclick="setHero(this, \'images/{fname}\')">\n'
            f'        <img src="images/{fname}" alt="">\n'
            f'      </button>\n'
        )

    # Specs
    spec_rows = [
        ("Year",           car_info.get("year")),
        ("Make &amp; Model", f"{car_info.get('make','')} {car_info.get('model','')}".strip() or None),
        ("Trim",           car_info.get("trim")),
        ("VIN",            car_info.get("vin")),
        ("Mileage",        car_info.get("mileage")),
        ("Exterior Color", car_info.get("exterior_color")),
        ("Interior",       car_info.get("interior_color")),
        ("Transmission",   car_info.get("transmission")),
        ("Drivetrain",     car_info.get("drivetrain")),
        ("Engine",         car_info.get("engine")),
        ("Condition",      car_info.get("condition")),
    ]
    specs_html = ""
    for label, val in spec_rows:
        if val:
            specs_html += (
                f'            <div class="spec-item">\n'
                f'              <dt>{label}</dt>\n'
                f'              <dd>{_esc(str(val))}</dd>\n'
                f'            </div>\n'
            )

    desc_html = _md_to_html(listing_text)
    subject = quote(f"Inquiry: {title}")

    inquiry_details = ""
    if car_info.get("condition"):
        inquiry_details += (
            f'            <div class="inquiry-detail">'
            f'<span>Condition</span><span>{_esc(car_info["condition"])}</span></div>\n'
        )
    if car_info.get("mileage"):
        inquiry_details += (
            f'            <div class="inquiry-detail">'
            f'<span>Mileage</span><span>{_esc(car_info["mileage"])}</span></div>\n'
        )
    inquiry_details += (
        f'            <div class="inquiry-detail">'
        f'<span>Listed</span><span>{month_year}</span></div>\n'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_esc(title)} &mdash; For Sale</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garant:wght@400;500;600&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="../../css/style.css">
  <link rel="stylesheet" href="../../css/listing.css">
  <style>
    .listing-description h3 {{
      font-family: var(--font-serif);
      font-size: 1.2rem;
      font-weight: 500;
      margin: 1.75rem 0 0.5rem;
      color: var(--text);
    }}
    .listing-description ul {{
      padding-left: 1.4rem;
      margin: 0.6rem 0 1rem;
    }}
    .listing-description li {{
      margin-bottom: 0.4rem;
      font-size: 1.02rem;
      line-height: 1.7;
    }}
    .listing-description .cta {{
      font-style: italic;
      color: var(--accent);
      margin-top: 1.75rem;
    }}
  </style>
</head>
<body>

  <header class="listing-header">
    <div class="container">
      <a href="../../" class="back-link">&#8592; All Listings</a>
    </div>
  </header>

  <div class="gallery">
    <div class="gallery-main">
      {gallery_main}
    </div>
    <div class="gallery-thumbs">
{thumbs_html}    </div>
  </div>

  <main>
    <div class="container listing-content">

      <div class="listing-main">

        <div class="listing-meta">
          <span class="listing-category">Automobile</span>
        </div>

        <h1 class="listing-title">{_esc(title)}</h1>

        <div class="listing-price">{price_str}</div>

        <div class="listing-description">
          {desc_html}
        </div>

        <div class="listing-specs">
          <h2>Specifications</h2>
          <dl class="specs-grid">
{specs_html}          </dl>
        </div>

      </div>

      <aside class="listing-sidebar">
        <div class="inquiry-card">
          <div class="inquiry-price">{price_str}</div>
          <p class="inquiry-note">Inspection welcome by appointment. Serious inquiries only.</p>
          <a href="mailto:{CONTACT_EMAIL}?subject={subject}" class="inquiry-btn">
            Send Inquiry
          </a>
          <div class="inquiry-details">
{inquiry_details}          </div>
        </div>
      </aside>

    </div>
  </main>

  <footer class="site-footer">
    <div class="container">
      <p>All items sold as-is. Serious inquiries only.</p>
    </div>
  </footer>

  <script>
    function setHero(btn, src) {{
      const hero = document.getElementById('gallery-hero');
      hero.style.opacity = '0';
      setTimeout(() => {{
        hero.src = src;
        hero.style.opacity = '1';
      }}, 150);
      document.querySelectorAll('.thumb').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
    }}
  </script>

</body>
</html>"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _title(car_info: dict) -> str:
    parts = [
        car_info.get("year", ""),
        car_info.get("make", ""),
        car_info.get("model", ""),
        car_info.get("trim", ""),
    ]
    return " ".join(p for p in parts if p).strip() or "Vehicle For Sale"


def _make_slug(car_info: dict) -> str:
    parts = [car_info.get("year", ""), car_info.get("make", ""), car_info.get("model", "")]
    raw = "-".join(p.lower() for p in parts if p)
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or "listing"


def _first_paragraph(text: str) -> str:
    """Return the first substantive non-heading paragraph from markdown."""
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
            line = re.sub(r"\*(.*?)\*", r"\1", line)
            if len(line) > 20:
                return line[:220] + ("…" if len(line) > 220 else "")
    return ""


def _md_to_html(text: str) -> str:
    """Convert Claude's structured markdown listing to HTML."""
    lines = text.strip().split("\n")
    out: list[str] = []
    in_ul = False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    def inline(s: str) -> str:
        s = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*(.*?)\*", r"<em>\1</em>", s)
        return s

    for raw in lines:
        line = raw.strip()
        if not line:
            close_ul()
            continue

        if line.startswith("# "):
            # Skip the headline — used as page title already
            continue

        if line.startswith("## "):
            close_ul()
            out.append(f"<h3>{_esc(line[3:])}</h3>")
            continue

        if line.startswith("- ") or line.startswith("* "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(_esc(line[2:]))}</li>")
            continue

        # Bold-only line = call to action
        close_ul()
        if re.fullmatch(r"\*\*.+\*\*", line):
            out.append(f'<p class="cta">{inline(_esc(line))}</p>')
        else:
            out.append(f"<p>{inline(_esc(line))}</p>")

    close_ul()
    return "\n          ".join(out)


def _esc(s: str) -> str:
    """Minimal HTML entity escaping."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
