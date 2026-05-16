# UI & code quality directives (developers)

Shared expectations for frontend work and for keeping CI / **SonarQube** reports clean.

---

## 1. Accessibility-aligned UI

Follow the intent of the MDN Accessibility documentation hub—not only color choice, but operability for assistive tech, keyboard users, motion sensitivity, and low vision. Primary entry point: **[Accessibility guides (MDN)](https://developer.mozilla.org/en-US/docs/Web/Accessibility/Guides)**.

### 1.1 Use project styling consistently

- **DaisyUI + theme tokens**: Prefer DaisyUI components and existing CSS variables (e.g. `--color-*`, theme from `theme.js`) so surfaces stay coherent and easier to tune for contrast.
- **Avoid one-off hex colors** in templates unless there is no token equivalent; pair with `:focus-visible` styles where custom controls are unavoidable.

### 1.2 Perceivable & understandable

- **Contrast & luminance**: Text and icons should remain readable against their background; interactive states (hover/focus/disabled) must remain distinguishable. See MDN’s *Web Accessibility: Understanding Colors and Luminance* (linked from the guides index above).
- **Color is not the only cue**: Errors, warnings, required fields, and status should include text or pattern, not color alone.

### 1.3 Operable

- **Keyboard**: All interactive flows reachable and usable via keyboard (`Tab`, `Enter`/`Space`, `Escape` for dismissals); visible **focus indicators** (`:focus-visible`) must not be removed without an equivalent replacement.
- **Semantics**: Prefer native HTML (`button`, `a`, `label`, headings, landmarks) before `div`-only widgets; when building custom widgets, plan for roles/labels per MDN/WAI-ARIA guidance (linked from MDN Accessibility guides).

### 1.4 Motion & vestibular safety

- Respect **`prefers-reduced-motion`** for non-essential animation; avoid flashes that could trigger seizures (see MDN topics on seizures / physical reactions and spatial patterns in the guides index).

### 1.5 Forms & messaging

- **Labels**: Every control has an associated `<label>` or `aria-labelledby` / `aria-label` where appropriate.
- **Feedback**: Prefer `aria-live` for important async updates where it matches the UX; keep log/status regions announced without flooding the screen reader.

### 1.6 Checklist before merge

- Spot-check contrast on **light + dark** theme if applicable.
- Tab through the changed screen without a mouse.
- Zoom to ~200% and confirm no critical controls are clipped or unreachable.

---

## 2. SonarQube–safe code

The repo combines **SonarQube/SonarScanner** (see `sonar-project.properties`; primary analyzed tree is **`aird/`**) with **eslint-plugin-sonarjs** on **`aird/static/js/**/*.js`** (see `eslint.config.mjs`). Generated or hand-written code should pass both where applicable.

### 2.1 JavaScript (SonarJS / ESLint)

- Run **`npm run lint:js`** before pushing; fix **errors** (not only warnings).
- **`aird/static/js/vendor/**` is ignored** by ESLint—**do not paste large copied libraries into linted paths**; keep third-party bundles under `vendor/` or add explicit ignores with team agreement.
- Common SonarJS themes: reduce **cognitive complexity** (extract helpers), avoid **duplicate branches**, replace **nested ternaries** where flagged, prefer **regex** bounded complexity, fix **promise / void misuse**, and eliminate **bugs** flagged as reliability issues.

### 2.2 Python (SonarScanner)

- Analyzed paths follow **`sonar.sources=aird`**; tests are excluded via `sonar.exclusions`.
- Aim for Clean Code rules your quality profile enables: clarity, manageable **functions/classes**, cautious use of **`except`**, no dead code paths, and security hotspots (injections, weak crypto, unsafe deserialization) addressed—not suppressed without review.

### 2.3 General

- **Do not widen exclusions** (Sonar or ESLint) solely to silence noise; fix root cause or open a deliberate team exception with rationale.
- New **minified or generated** blobs belong in **`vendor/`** or similar and stay out of `sonar.sources` if policy says so—or remain excluded explicitly.

---

## 3. Where to look in this repo

| Area | Reference |
|------|-----------|
| JS lint / Sonar-style rules | `eslint.config.mjs`, `npm run lint:js` |
| Sonar scan scope | `sonar-project.properties` |
| Theming | `aird/static/js/theme.js`, DaisyUI/Tailwind in `src/input.css` → `aird/static/css/app.css` |

For accessibility depth beyond MDN’s hub, WCAG summaries linked from **[Understanding WCAG (MDN)](https://developer.mozilla.org/en-US/docs/Web/Accessibility/Guides)** (guide list) remain the canonical bar for conformance discussions.
