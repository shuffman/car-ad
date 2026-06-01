/* ════════════════════════════════════════
   UPLOAD PAGE
   ════════════════════════════════════════ */
if (document.getElementById('uploadForm')) {
  const form       = document.getElementById('uploadForm');
  const dropZone   = document.getElementById('dropZone');
  const fileInput  = document.getElementById('fileInput');
  const previewGrid = document.getElementById('previewGrid');
  const dropPrompt = document.getElementById('dropPrompt');
  const fileCountRow = document.getElementById('fileCountRow');
  const fileCountLabel = document.getElementById('fileCountLabel');
  const clearBtn   = document.getElementById('clearBtn');
  const submitBtn  = document.getElementById('submitBtn');
  const overlay    = document.getElementById('processingOverlay');
  const gdriveInput = document.getElementById('gdriveUrl');

  let files = [];

  const PROCESSING_STEPS = [
    'Downloading files…',
    'Enhancing photos…',
    'Analyzing vehicle…',
    'Writing your listing…',
  ];

  // ── Drop zone interactions ──

  dropZone.addEventListener('click', e => {
    if (!e.target.closest('.remove-file-btn')) fileInput.click();
  });

  dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
  });
  ['dragleave', 'dragend'].forEach(ev =>
    dropZone.addEventListener(ev, () => dropZone.classList.remove('drag-over'))
  );
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    addFiles([...e.dataTransfer.files]);
  });

  fileInput.addEventListener('change', () => {
    addFiles([...fileInput.files]);
    fileInput.value = '';
  });

  clearBtn.addEventListener('click', e => {
    e.stopPropagation();
    files = [];
    render();
  });

  // ── Drive URL shows analyze hint ──
  gdriveInput?.addEventListener('input', () => {
    updateAnalyzeVisibility();
  });

  function updateAnalyzeVisibility() {
    const hasFiles = files.length > 0;
    const hasUrl = gdriveInput && gdriveInput.value.trim().length > 0;
    // (no analyze button in new design — just drives submit readiness)
  }

  function addFiles(incoming) {
    const imageTypes = new Set(['image/jpeg','image/png','image/webp','image/bmp','image/tiff','image/heic']);
    const valid = incoming.filter(f =>
      imageTypes.has(f.type) || f.type === 'application/pdf' ||
      f.name.match(/\.(jpe?g|png|webp|bmp|tiff?|heic?|pdf)$/i)
    );
    const remaining = Math.max(0, 20 - files.length);
    files = [...files, ...valid.slice(0, remaining)];
    render();
  }

  function removeFile(idx) {
    files.splice(idx, 1);
    render();
  }

  function render() {
    if (files.length === 0) {
      previewGrid.classList.add('hidden');
      dropPrompt.classList.remove('hidden');
      fileCountRow.classList.add('hidden');
      syncInput();
      return;
    }

    dropPrompt.classList.add('hidden');
    previewGrid.classList.remove('hidden');
    fileCountRow.classList.remove('hidden');

    const nPdf = files.filter(f => f.name.toLowerCase().endsWith('.pdf')).length;
    const nImg = files.length - nPdf;
    const parts = [];
    if (nImg) parts.push(`${nImg} photo${nImg !== 1 ? 's' : ''}`);
    if (nPdf) parts.push(`${nPdf} PDF${nPdf !== 1 ? 's' : ''}`);
    fileCountLabel.textContent = parts.join(', ') + ' selected';

    previewGrid.innerHTML = '';
    files.forEach((file, i) => {
      const div = document.createElement('div');
      div.className = 'preview-item';

      const isPdf = file.name.toLowerCase().endsWith('.pdf');
      if (isPdf) {
        div.innerHTML = `<div class="pdf-tile">
          <i class="bi bi-file-earmark-pdf-fill"></i>
          <span>${file.name}</span>
        </div>`;
      } else {
        const img = document.createElement('img');
        const url = URL.createObjectURL(file);
        img.src = url;
        img.onload = () => URL.revokeObjectURL(url);
        div.appendChild(img);
      }

      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'remove-file-btn';
      rm.textContent = '×';
      rm.addEventListener('click', e => { e.stopPropagation(); removeFile(i); });
      div.appendChild(rm);

      previewGrid.appendChild(div);
    });

    syncInput();
  }

  function syncInput() {
    const dt = new DataTransfer();
    files.forEach(f => dt.items.add(f));
    fileInput.files = dt.files;
  }

  // ── Submit with loading overlay ──

  const stepEl = document.getElementById('processingLabel');
  let stepIdx = 0;

  function cycleSteps() {
    if (!stepEl) return;
    stepEl.style.opacity = '0';
    setTimeout(() => {
      stepIdx = (stepIdx + 1) % PROCESSING_STEPS.length;
      stepEl.textContent = PROCESSING_STEPS[stepIdx];
      stepEl.style.opacity = '1';
    }, 300);
  }

  form.addEventListener('submit', () => {
    overlay.classList.remove('hidden');
    submitBtn.disabled = true;
    if (stepEl) {
      stepEl.textContent = PROCESSING_STEPS[0];
      setInterval(cycleSteps, 4000);
    }
  });
}


/* ════════════════════════════════════════
   WORKSPACE / RESULT PAGE
   ════════════════════════════════════════ */
if (typeof RAW_LISTING !== 'undefined') {
  const editor = document.getElementById('listingEditor');
  const hero   = document.getElementById('galleryHero');

  // ── Render listing into contenteditable ──
  if (editor && typeof marked !== 'undefined') {
    editor.innerHTML = marked.parse(RAW_LISTING);
  }

  // ── Gallery navigation ──
  let currentIdx = 0;

  window.showPhoto = function(btn, idx) {
    currentIdx = idx;
    if (hero) {
      hero.style.opacity = '0';
      setTimeout(() => {
        hero.src = `/image/${RESULT_ID}/${idx}?v=${Date.now()}`;
        hero.style.opacity = '1';
      }, 120);
    }
    document.querySelectorAll('.thumb-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const dl = document.getElementById('downloadBtn');
    if (dl) dl.href = `/download/${RESULT_ID}/${idx}`;
  };

  // ── Enhancement presets ──
  document.querySelectorAll('.preset-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const preset = btn.dataset.preset;
      document.querySelectorAll('.preset-btn').forEach(b => {
        b.classList.remove('active');
        b.disabled = true;
      });
      btn.classList.add('active');

      try {
        await fetch(`/enhance/${RESULT_ID}/${preset}`, { method: 'POST' });
        // Reload all images with cache-buster
        const v = Date.now();
        if (hero) hero.src = `/image/${RESULT_ID}/${currentIdx}?v=${v}`;
        document.querySelectorAll('.thumb-btn img').forEach((img, i) => {
          img.src = `/image/${RESULT_ID}/${i}?v=${v}`;
        });
      } catch (e) {
        console.error('Enhance failed', e);
      } finally {
        document.querySelectorAll('.preset-btn').forEach(b => b.disabled = false);
      }
    });
  });

  // ── Regenerate ──
  document.getElementById('regenerateBtn')?.addEventListener('click', async () => {
    if (!confirm('Replace the current text with a new AI-generated version?')) return;

    const btn = document.getElementById('regenerateBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-arrow-clockwise spin"></i> Regenerating…';
    if (editor) editor.style.opacity = '0.35';

    try {
      const res = await fetch(`/regenerate/${RESULT_ID}`, { method: 'POST' });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      if (editor) editor.innerHTML = marked.parse(data.listing_text);
    } catch (e) {
      alert('Regeneration failed: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Regenerate';
      if (editor) editor.style.opacity = '1';
    }
  });

  // ── Copy ──
  document.getElementById('copyBtn')?.addEventListener('click', async () => {
    const btn = document.getElementById('copyBtn');
    try {
      await navigator.clipboard.writeText(editor?.innerText || '');
      btn.innerHTML = '<i class="bi bi-check2"></i> Copied!';
      setTimeout(() => { btn.innerHTML = '<i class="bi bi-clipboard"></i> Copy'; }, 2200);
    } catch {
      btn.textContent = 'Copy failed';
    }
  });

  // ── Publish ──
  document.getElementById('publishBtn')?.addEventListener('click', async () => {
    const btn    = document.getElementById('publishBtn');
    const status = document.getElementById('publishStatus');

    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-cloud-arrow-up"></i> Publishing…';
    if (status) { status.className = 'publish-status hidden'; }

    try {
      const res = await fetch(`/deploy/${RESULT_ID}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          listing_html: editor?.innerHTML || '',
          listing_text: editor?.innerText || '',
        }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      if (status) {
        status.className = 'publish-status success';
        status.innerHTML = `Published! <a href="${data.url}" target="_blank">${data.url}</a>`
          + ` &mdash; live in ~60s`;
      }
      btn.innerHTML = '<i class="bi bi-check2"></i> Published';
      btn.classList.add('success');
    } catch (e) {
      if (status) {
        status.className = 'publish-status error';
        status.textContent = 'Publish failed: ' + e.message;
      }
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-github"></i> Publish to forsale';
    }
  });
}
