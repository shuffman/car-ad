/* ── Index page ── */
if (document.getElementById('carForm')) {
  const dropZone = document.getElementById('dropZone');
  const imageInput = document.getElementById('imageInput');
  const previewGrid = document.getElementById('previewGrid');
  const dropPrompt = document.getElementById('dropPrompt');
  const photoCount = document.getElementById('photoCount');
  const clearBtn = document.getElementById('clearBtn');
  const submitBtn = document.getElementById('submitBtn');
  const form = document.getElementById('carForm');

  let selectedFiles = [];

  dropZone.addEventListener('click', (e) => {
    if (!e.target.classList.contains('remove-photo')) imageInput.click();
  });

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
  });

  ['dragleave', 'dragend'].forEach(ev =>
    dropZone.addEventListener(ev, () => dropZone.classList.remove('drag-over'))
  );

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    addFiles([...e.dataTransfer.files].filter(f => f.type.startsWith('image/')));
  });

  imageInput.addEventListener('change', () => {
    addFiles([...imageInput.files]);
    imageInput.value = '';
  });

  clearBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    selectedFiles = [];
    renderPreviews();
  });

  function addFiles(files) {
    const slots = Math.max(0, 10 - selectedFiles.length);
    selectedFiles = [...selectedFiles, ...files.slice(0, slots)];
    renderPreviews();
  }

  function removeFile(idx) {
    selectedFiles.splice(idx, 1);
    renderPreviews();
  }

  function renderPreviews() {
    updateAnalyzeVisibility();

    if (selectedFiles.length === 0) {
      previewGrid.classList.add('d-none');
      dropPrompt.style.display = '';
      photoCount.textContent = 'No photos selected';
      clearBtn.classList.add('d-none');
      syncInput();
      return;
    }

    dropPrompt.style.display = 'none';
    previewGrid.classList.remove('d-none');
    clearBtn.classList.remove('d-none');
    const nPdf = selectedFiles.filter(f => f.name.toLowerCase().endsWith('.pdf')).length;
    const nImg = selectedFiles.length - nPdf;
    const parts = [];
    if (nImg) parts.push(`${nImg} photo${nImg !== 1 ? 's' : ''}`);
    if (nPdf) parts.push(`${nPdf} PDF${nPdf !== 1 ? 's' : ''}`);
    photoCount.textContent = parts.join(', ') + ' selected';

    previewGrid.innerHTML = '';
    selectedFiles.forEach((file, i) => {
      const div = document.createElement('div');
      const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
      div.className = isPdf ? 'preview-item pdf-item' : 'preview-item';

      if (isPdf) {
        const icon = document.createElement('div');
        icon.className = 'pdf-preview';
        icon.innerHTML = `<i class="bi bi-file-earmark-pdf-fill"></i><span>${file.name}</span>`;
        div.appendChild(icon);
      } else {
        const img = document.createElement('img');
        const url = URL.createObjectURL(file);
        img.src = url;
        img.alt = file.name;
        img.onload = () => URL.revokeObjectURL(url);
        div.appendChild(img);
      }

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'remove-photo';
      btn.title = 'Remove';
      btn.textContent = '×';
      btn.addEventListener('click', (e) => { e.stopPropagation(); removeFile(i); });

      div.appendChild(btn);
      previewGrid.appendChild(div);
    });

    syncInput();
  }

  function syncInput() {
    const dt = new DataTransfer();
    selectedFiles.forEach(f => dt.items.add(f));
    imageInput.files = dt.files;
  }

  // Show/hide the analyze button whenever photos or Drive URL change
  const analyzeSection = document.getElementById('analyzeSection');
  const analyzeBtn = document.getElementById('analyzeBtn');
  const analyzeStatus = document.getElementById('analyzeStatus');
  const gdriveInput = document.getElementById('gdriveUrl');

  function updateAnalyzeVisibility() {
    const hasPhotos = selectedFiles.length > 0;
    const hasDrive = gdriveInput && gdriveInput.value.trim().length > 0;
    analyzeSection.classList.toggle('d-none', !hasPhotos && !hasDrive);
  }

  if (gdriveInput) {
    gdriveInput.addEventListener('input', updateAnalyzeVisibility);
  }

  // ── Analyze handler ──
  analyzeBtn.addEventListener('click', async () => {
    analyzeBtn.disabled = true;
    analyzeBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Analyzing photos…';
    analyzeStatus.textContent = '';
    analyzeStatus.className = 'form-text text-center mt-1';

    const fd = new FormData();
    selectedFiles.forEach(f => fd.append('images', f));
    if (gdriveInput && gdriveInput.value.trim()) {
      fd.append('gdrive_url', gdriveInput.value.trim());
    }

    try {
      const res = await fetch('/analyze', { method: 'POST', body: fd });
      const data = await res.json();

      if (data.error) throw new Error(data.error);

      const filled = populateForm(data);
      analyzeStatus.textContent = filled > 0
        ? `✓ Detected ${filled} field${filled !== 1 ? 's' : ''} — review and adjust below`
        : 'No additional details detected from photos';
      analyzeStatus.className = 'form-text text-center mt-1 text-success';
    } catch (err) {
      analyzeStatus.textContent = `Could not analyze: ${err.message}`;
      analyzeStatus.className = 'form-text text-center mt-1 text-danger';
    } finally {
      analyzeBtn.disabled = false;
      analyzeBtn.innerHTML = '<i class="bi bi-robot me-2"></i>Re-analyze photos';
    }
  });

  // Map of detected JSON keys → form field IDs
  const FIELD_MAP = {
    year: 'f_year', make: 'f_make', model: 'f_model', trim: 'f_trim',
    exterior_color: 'f_exterior_color', interior_color: 'f_interior_color',
    condition: 'f_condition', transmission: 'f_transmission',
    drivetrain: 'f_drivetrain', engine: 'f_engine',
    features: 'f_features', notes: 'f_notes',
  };

  function populateForm(data) {
    let filled = 0;
    for (const [key, elId] of Object.entries(FIELD_MAP)) {
      const val = data[key];
      if (!val) continue;
      const el = document.getElementById(elId);
      if (!el) continue;

      if (el.tagName === 'SELECT') {
        const lower = val.toLowerCase();
        let matched = false;
        for (const opt of el.options) {
          if (opt.value && opt.text.toLowerCase().startsWith(lower.slice(0, 4))) {
            el.value = opt.value;
            matched = true;
            break;
          }
        }
        if (!matched) continue;
      } else {
        if (el.value) continue; // don't overwrite existing input
        el.value = val;
      }

      // Flash blue highlight to show it was AI-detected
      el.classList.add('ai-detected');
      setTimeout(() => el.classList.remove('ai-detected'), 3000);
      filled++;
    }
    return filled;
  }

  const processingModal = new bootstrap.Modal(document.getElementById('processingModal'));

  form.addEventListener('submit', (e) => {
    processingModal.show();
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Generating…';
  });
}


/* ── Result page ── */
if (typeof RAW_LISTING !== 'undefined') {
  // Render markdown listing
  const listingEl = document.getElementById('listingText');
  if (listingEl && typeof marked !== 'undefined') {
    listingEl.innerHTML = marked.parse(RAW_LISTING);
  } else if (listingEl) {
    listingEl.textContent = RAW_LISTING;
  }

  // Copy button
  const copyBtn = document.getElementById('copyBtn');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(RAW_LISTING);
        copyBtn.innerHTML = '<i class="bi bi-check2 me-1"></i>Copied!';
        copyBtn.classList.replace('btn-outline-primary', 'btn-success');
        setTimeout(() => {
          copyBtn.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copy';
          copyBtn.classList.replace('btn-success', 'btn-outline-primary');
        }, 2500);
      } catch {
        copyBtn.textContent = 'Copy failed';
      }
    });
  }

  // Lightbox
  let currentIdx = 0;
  const lightboxModal = document.getElementById('lightboxModal')
    ? new bootstrap.Modal(document.getElementById('lightboxModal'))
    : null;

  window.openLightbox = function(index) {
    if (!lightboxModal) return;
    currentIdx = index;
    updateLightbox();
    lightboxModal.show();
  };

  function updateLightbox() {
    const img = document.getElementById('lightboxImg');
    const dl = document.getElementById('lightboxDownload');
    const caption = document.getElementById('lightboxCaption');
    if (!img) return;
    img.src = `/image/${RESULT_ID}/${currentIdx}`;
    dl.href = `/download/${RESULT_ID}/${currentIdx}`;
    dl.download = `enhanced-photo-${currentIdx + 1}.jpg`;
    if (caption) caption.textContent = `Photo ${currentIdx + 1} of ${IMAGE_COUNT}`;
    document.getElementById('lightboxPrev').disabled = currentIdx === 0;
    document.getElementById('lightboxNext').disabled = currentIdx === IMAGE_COUNT - 1;
  }

  const prevBtn = document.getElementById('lightboxPrev');
  const nextBtn = document.getElementById('lightboxNext');
  if (prevBtn) prevBtn.addEventListener('click', () => { if (currentIdx > 0) { currentIdx--; updateLightbox(); } });
  if (nextBtn) nextBtn.addEventListener('click', () => { if (currentIdx < IMAGE_COUNT - 1) { currentIdx++; updateLightbox(); } });

  // Click photo grid items to open lightbox
  document.querySelectorAll('.photo-item').forEach(item => {
    item.addEventListener('click', (e) => {
      if (e.target.closest('a, button')) return;
      openLightbox(parseInt(item.dataset.index));
    });
  });

  // ── Deploy to forsale ──
  const deployBtn = document.getElementById('deployBtn');
  const deployResult = document.getElementById('deployResult');

  if (deployBtn) {
    deployBtn.addEventListener('click', async () => {
      deployBtn.disabled = true;
      deployBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Publishing…';
      deployResult.className = 'mt-3';
      deployResult.innerHTML = '<div class="text-muted small">Uploading photos and pushing to GitHub — this takes about 20–40 seconds…</div>';

      try {
        const res = await fetch(`/deploy/${RESULT_ID}`, { method: 'POST' });
        const data = await res.json();

        if (data.error) throw new Error(data.error);

        deployResult.innerHTML =
          `<div class="alert alert-success d-flex align-items-center gap-3 mb-0">` +
          `<i class="bi bi-check-circle-fill fs-4"></i>` +
          `<div>` +
          `<div class="fw-semibold">Published!</div>` +
          `<div class="small">Live at <a href="${data.url}" target="_blank" class="alert-link">${data.url}</a>` +
          ` — GitHub Pages may take up to 60 seconds to reflect the change.</div>` +
          `</div></div>`;
        deployBtn.innerHTML = '<i class="bi bi-check2 me-2"></i>Published';
        deployBtn.classList.replace('btn-dark', 'btn-success');
      } catch (err) {
        deployResult.innerHTML =
          `<div class="alert alert-danger mb-0"><i class="bi bi-exclamation-triangle-fill me-2"></i>${err.message}</div>`;
        deployBtn.disabled = false;
        deployBtn.innerHTML = '<i class="bi bi-cloud-arrow-up me-2"></i>Retry';
      }
    });
  }
}
