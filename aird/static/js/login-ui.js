/* Shared login-page UI: tab switcher, paste button, password eye toggle */
(function (global) {
  function safeSessionGet(key) {
    try {
      return sessionStorage.getItem(key) || '';
    } catch (e) {
      console.debug('sessionStorage read failed:', e.message);
      return '';
    }
  }

  function safeSessionSet(key, val) {
    try {
      sessionStorage.setItem(key, val);
    } catch (e) {
      console.debug('sessionStorage write failed:', e.message);
    }
  }

  function initLoginUI(cfg) {
    var tabCreds  = document.getElementById(cfg.tabCreds);
    var tabToken  = document.getElementById(cfg.tabToken);
    var formCreds = document.getElementById(cfg.formCreds);
    var formToken = document.getElementById(cfg.formToken);
    var indicator = document.getElementById(cfg.indicator);
    var switcher  = document.getElementById(cfg.switcher);
    var storageKey = cfg.storageKey || 'aird-tab';

    if (!tabCreds || !tabToken || !formCreds || !formToken) return;

    function moveIndicator(tok) {
      if (!indicator || !switcher) return;
      var half = (switcher.offsetWidth - 8) / 2;
      indicator.style.width = half + 'px';
      indicator.style.left  = (tok ? half + 4 : 4) + 'px';
    }

    function applyFadeIn(el) {
      el.style.opacity   = '1';
      el.style.transform = 'translateY(0)';
    }

    function fadeIn(el) {
      el.style.display    = '';
      el.style.opacity    = '0';
      el.style.transform  = 'translateY(6px)';
      el.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
      requestAnimationFrame(function () {
        requestAnimationFrame(applyFadeIn.bind(null, el));
      });
    }

    function switchTab(name) {
      var tok = name === 'token';
      if (tok) { formCreds.style.display = 'none'; fadeIn(formToken); }
      else      { formToken.style.display = 'none'; fadeIn(formCreds); }
      tabCreds.style.opacity = tok ? '0.45' : '1';
      tabToken.style.opacity = tok ? '1'    : '0.45';
      moveIndicator(tok);
      var focusEl = document.getElementById(tok ? cfg.tokenArea : cfg.usernameField);
      if (focusEl) setTimeout(function () { focusEl.focus(); }, 50);
      safeSessionSet(storageKey, name);
    }

    window.addEventListener('resize', function () {
      moveIndicator(tabToken.style.opacity === '1');
    });

    tabCreds.addEventListener('click', function () { switchTab('creds'); });
    tabToken.addEventListener('click', function () { switchTab('token'); });

    formCreds.addEventListener('submit', function () { safeSessionSet(storageKey, 'creds'); });
    formToken.addEventListener('submit', function () { safeSessionSet(storageKey, 'token'); });

    window.addEventListener('load', function () {
      var saved = safeSessionGet(storageKey);
      if (saved === 'token') {
        formCreds.style.display = 'none';
        formToken.style.display = '';
        tabCreds.style.opacity  = '0.45';
        tabToken.style.opacity  = '1';
      }
      moveIndicator(saved === 'token');
    });

    /* paste button: focus textarea so user can Ctrl+V without browser dialog */
    var pasteBtn   = document.getElementById(cfg.pasteBtn);
    var pasteLabel = document.getElementById(cfg.pasteLabel);
    if (pasteBtn) {
      pasteBtn.addEventListener('click', function () {
        var ta = document.getElementById(cfg.tokenArea);
        if (!ta) return;
        ta.focus();
        ta.select();
        if (pasteLabel) {
          pasteLabel.textContent = 'Ctrl+V';
          setTimeout(function () { pasteLabel.textContent = 'Paste'; }, 1800);
        }
      });
    }

    /* password show/hide eye toggle */
    var pwdInput = document.getElementById(cfg.eyeInput);
    var pwdEye   = document.getElementById(cfg.eyeBtn);
    var pwdIcon  = document.getElementById(cfg.eyeIcon);
    var eyeOff = '<path stroke-linecap="round" stroke-linejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"/>';
    var eyeOn  = '<path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>';
    if (pwdEye && pwdInput && pwdIcon) {
      pwdEye.addEventListener('click', function () {
        var show = pwdInput.type === 'password';
        pwdInput.type    = show ? 'text' : 'password';
        pwdIcon.innerHTML = show ? eyeOff : eyeOn;
      });
    }
  }

  global.AirdLoginUI = { init: initLoginUI };
}(globalThis));
