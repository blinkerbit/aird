/* Animated background — unique palette per DaisyUI theme */
/* eslint-disable sonarjs/pseudo-random -- decorative animation only */
(function () {
  var canvas = document.getElementById('bg-canvas');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var W = 0, H = 0;

  /* ── Per-theme palettes [r,g,b] × 6 orbs ── */
  var PALETTES = {
    light:    [[99,102,241],[168,85,247],[236,72,153],[34,197,94],[234,179,8],[59,130,246]],
    dark:     [[99,102,241],[139,92,246],[59,130,246],[20,184,166],[245,158,11],[236,72,153]],
    nord:     [[136,192,208],[129,161,193],[94,129,172],[163,190,140],[235,203,139],[191,97,106]],
    dracula:  [[189,147,249],[255,121,198],[80,250,123],[241,250,140],[255,184,108],[255,85,85]],
    cyberpunk:[[255,232,0],[255,0,200],[0,240,255],[255,100,0],[180,0,255],[0,255,140]],
    retro:    [[239,131,84],[220,98,51],[192,160,128],[245,208,96],[143,82,71],[180,130,80]],
    autumn:   [[220,88,42],[196,57,19],[240,160,30],[139,69,19],[180,100,40],[200,60,30]],
    _default: [[99,102,241],[168,85,247],[236,72,153],[34,197,94],[234,179,8],[59,130,246]],
  };

  /* ── Orb layout (position + motion params) ── */
  var LAYOUT = [
    { bx:0.15, by:0.22, fx:0.41, fy:0.31, r:0.55 },
    { bx:0.82, by:0.16, fx:0.27, fy:0.43, r:0.48 },
    { bx:0.52, by:0.8,  fx:0.33, fy:0.22, r:0.52 },
    { bx:0.08, by:0.68, fx:0.19, fy:0.38, r:0.4  },
    { bx:0.9,  by:0.6,  fx:0.29, fy:0.25, r:0.43 },
    { bx:0.62, by:0.05, fx:0.22, fy:0.35, r:0.38 },
  ];

  var orbs = LAYOUT.map(function (l) {
    return {
      bx: l.bx, by: l.by, fx: l.fx, fy: l.fy, r: l.r,
      t: Math.random() * Math.PI * 2, // NOSONAR - visual animation only, not security-sensitive
      rgb: [128, 128, 200],
      target: [128, 128, 200],
    };
  });

  function getTheme() {
    return document.documentElement.dataset.theme || 'light';
  }

  function syncPalette() {
    var pal = PALETTES[getTheme()] || PALETTES['_default'];
    orbs.forEach(function (o, i) { o.target = pal[i].slice(); });
  }

  var stars = [];

  function initStars() {
    stars = [];
    var n = Math.min(110, Math.floor(W * H / 8500));
    for (var i = 0; i < n; i++) {
      stars.push({
        x:    Math.random() * W,           // NOSONAR - visual animation only, not security-sensitive
        y:    Math.random() * H,           // NOSONAR - visual animation only, not security-sensitive
        r:    0.4 + Math.random() * 1.4,  // NOSONAR - visual animation only, not security-sensitive
        a:    0.1 + Math.random() * 0.5,  // NOSONAR - visual animation only, not security-sensitive
        ta:   0.1 + Math.random() * 0.5,  // NOSONAR - visual animation only, not security-sensitive
        aspd: 0.004 + Math.random() * 0.008, // NOSONAR - visual animation only, not security-sensitive
        vx:   (Math.random() - 0.5) * 0.22, // NOSONAR - visual animation only, not security-sensitive
        vy:   (Math.random() - 0.5) * 0.22, // NOSONAR - visual animation only, not security-sensitive
      });
    }
  }

  function init() {
    W = canvas.width  = globalThis.innerWidth;
    H = canvas.height = globalThis.innerHeight;
    syncPalette();
    orbs.forEach(function (o) { o.rgb = o.target.slice(); });
    initStars();
  }

  var lastTs = 0;

  function tick(ts) {
    var dt = Math.min(ts - lastTs, 50);
    lastTs = ts;
    ctx.clearRect(0, 0, W, H);

    var i, o, s, cx, cy, rad, r, g, b, gr;
    for (i = 0; i < orbs.length; i++) {
      o = orbs[i];
      o.t += dt * 0.00035;
      for (var ch = 0; ch < 3; ch++) {
        o.rgb[ch] += (o.target[ch] - o.rgb[ch]) * 0.028;
      }
      cx  = (o.bx + Math.sin(o.t * o.fx) * 0.17) * W;
      cy  = (o.by + Math.cos(o.t * o.fy) * 0.17) * H;
      rad = o.r * Math.max(W, H);
      r = Math.trunc(o.rgb[0]);
      g = Math.trunc(o.rgb[1]);
      b = Math.trunc(o.rgb[2]);

      gr = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
      gr.addColorStop(0,    'rgba('+r+','+g+','+b+',0.42)');
      gr.addColorStop(0.35, 'rgba('+r+','+g+','+b+',0.18)');
      gr.addColorStop(0.7,  'rgba('+r+','+g+','+b+',0.05)');
      gr.addColorStop(1,    'rgba('+r+','+g+','+b+',0)');

      ctx.beginPath();
      ctx.arc(cx, cy, rad, 0, Math.PI * 2);
      ctx.fillStyle = gr;
      ctx.fill();
    }

    for (i = 0; i < stars.length; i++) {
      s = stars[i];
      s.x += s.vx; s.y += s.vy;
      if (s.x < 0) s.x = W; else if (s.x > W) s.x = 0;
      if (s.y < 0) s.y = H; else if (s.y > H) s.y = 0;
      if (Math.abs(s.a - s.ta) < 0.015) s.ta = 0.05 + Math.random() * 0.7; // NOSONAR - visual animation only, not security-sensitive
      s.a += (s.ta - s.a) * s.aspd;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,255,255,' + s.a.toFixed(2) + ')';
      ctx.fill();
    }

    requestAnimationFrame(tick);
  }

  init();
  requestAnimationFrame(tick);
  globalThis.addEventListener('resize', init);
  new MutationObserver(syncPalette)
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}());
