(function () {
  'use strict';

  const kind = document.body.dataset.mediaKind;
  const stage = document.getElementById('media-stage');
  const errorEl = document.getElementById('media-load-error');

  function showError(message) {
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.classList.remove('hidden');
    if (stage) stage.classList.add('hidden');
  }

  if (kind === 'image') {
    const img = document.getElementById('media-image');
    if (!img) return;

    let scale = 1;

    function applyScale() {
      img.style.transform = 'scale(' + scale + ')';
    }

    img.addEventListener('error', function () {
      showError('Could not load image. Try downloading the file instead.');
    });

    document.getElementById('media-fit-btn')?.addEventListener('click', function () {
      scale = 1;
      img.style.maxWidth = '100%';
      img.style.maxHeight = '80vh';
      applyScale();
    });

    document.getElementById('media-zoom-in-btn')?.addEventListener('click', function () {
      scale = Math.min(scale * 1.25, 8);
      img.style.maxWidth = 'none';
      img.style.maxHeight = 'none';
      applyScale();
    });

    document.getElementById('media-zoom-out-btn')?.addEventListener('click', function () {
      scale = Math.max(scale / 1.25, 0.25);
      applyScale();
    });

    document.getElementById('media-reset-btn')?.addEventListener('click', function () {
      scale = 1;
      img.style.maxWidth = '100%';
      img.style.maxHeight = '80vh';
      applyScale();
    });
  }

  if (kind === 'pdf') {
    const src = document.body.dataset.mediaSrc;
    const loadingEl = document.getElementById('media-pdf-loading');
    if (!src || !stage) return;

    function clearLoading() {
      if (loadingEl) loadingEl.remove();
    }

    fetch(src, { credentials: 'same-origin' })
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error('HTTP ' + resp.status);
        }
        const ct = (resp.headers.get('content-type') || '').toLowerCase();
        if (ct && !ct.includes('pdf') && !ct.includes('octet-stream')) {
          throw new Error('Unexpected response type');
        }
        return resp.blob();
      })
      .then(function (blob) {
        clearLoading();
        const url = URL.createObjectURL(blob);
        const embed = document.createElement('embed');
        embed.type = 'application/pdf';
        embed.src = url;
        embed.title = document.title.replace(/ · Aird$/, '');
        embed.className = 'w-full';
        embed.style.width = '100%';
        embed.style.height = '80vh';
        embed.style.minHeight = '480px';
        stage.appendChild(embed);
      })
      .catch(function () {
        clearLoading();
        showError('Could not load PDF. Try downloading the file instead.');
      });
  }
})();
