// Theme Manager - handles multi-theme switching with cookie persistence
// DaisyUI themes are activated via data-theme on <html>

(function applySavedThemeBeforePaint() {
    const THEMES = ['light', 'dark', 'nord', 'dracula', 'cyberpunk', 'retro', 'autumn'];
    const DARK = { dark: true, dracula: true };
    const BG = {
        light: '#ffffff', dark: '#1d232a', nord: '#eceff4', dracula: '#282a36',
        cyberpunk: '#ffee00', retro: '#e4d8b4', autumn: '#faf1e9',
    };
    const key = 'aird_theme';
    const keyEsc = key.replaceAll(/[\\^$*+?.()|[\]{}]/g, (ch) => String.fromCodePoint(92) + ch);
    const re = new RegExp('(?:^|; )' + keyEsc + '=([^;]*)');
    const m = re.exec(document.cookie);
    let theme = m ? decodeURIComponent(m[1]) : null;
    if (!theme || !THEMES.includes(theme)) {
        theme = globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    const root = document.documentElement;
    if (!root.classList.contains('theme-early')) {
        root.dataset.theme = theme;
        root.style.colorScheme = DARK[theme] ? 'dark' : 'light';
        root.style.backgroundColor = BG[theme] || BG.light;
    }
})();

class ThemeManager {
    get storageKey() {
        return 'aird_theme';
    }

    get availableThemes() {
        return [
            { id: 'light',     label: '☀️ Light',     dark: false },
            { id: 'dark',      label: '🌙 Dark',      dark: true  },
            { id: 'nord',      label: '❄️ Nord',      dark: false },
            { id: 'dracula',   label: '🧛 Dracula',   dark: true  },
            { id: 'cyberpunk', label: '🤖 Cyberpunk', dark: false },
            { id: 'retro',     label: '📺 Retro',     dark: false },
            { id: 'autumn',    label: '🍂 Autumn',    dark: false },
        ];
    }

    constructor() {
        this.init();
    }

    init() {
        const saved = this.getSavedTheme();
        if (saved && this.availableThemes.some(t => t.id === saved)) {
            this.setTheme(saved, false);
        } else if (globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches) {
            this.setTheme('dark', false);
        } else {
            this.setTheme('light', false);
        }
        this.bindToggleButtons();
        this.buildThemeDropdowns();
        this.bindThemeSelectorLinks();
    }

    bindThemeSelectorLinks() {
        document.querySelectorAll('[data-set-theme]').forEach((el) => {
            el.addEventListener('click', (e) => {
                e.preventDefault();
                const theme = e.currentTarget.dataset.setTheme;
                this.setTheme(theme);
            });
        });
    }

    getSavedTheme() {
        const keyEsc = this.storageKey.replaceAll(
            /[\\^$*+?.()|[\]{}]/g,
            (ch) => String.fromCodePoint(92) + ch
        );
        const re = new RegExp('(?:^|; )' + keyEsc + '=([^;]*)');
        const m = re.exec(document.cookie);
        return m ? decodeURIComponent(m[1]) : null;
    }

    saveTheme(theme) {
        const expires = new Date(Date.now() + 365 * 864e5).toUTCString();
        const secure = globalThis.location?.protocol === 'https:' ? '; Secure' : '';
        document.cookie = this.storageKey + '=' + encodeURIComponent(theme) +
            '; expires=' + expires + '; path=/; SameSite=Lax' + secure;
    }

    setTheme(theme, save) {
        const DARK = { dark: true, dracula: true };
        const BG = {
            light: '#ffffff', dark: '#1d232a', nord: '#eceff4', dracula: '#282a36',
            cyberpunk: '#ffee00', retro: '#e4d8b4', autumn: '#faf1e9',
        };
        const root = document.documentElement;
        root.dataset.theme = theme;
        root.style.colorScheme = DARK[theme] ? 'dark' : 'light';
        root.style.backgroundColor = BG[theme] || BG.light;
        if (save !== false) {
            this.saveTheme(theme);
        }
        document.querySelectorAll('.theme-select').forEach((sel) => {
            sel.value = theme;
        });
        const matched = this.availableThemes.find((t) => t.id === theme);
        const label = matched?.dark ? `Current: ${theme} (dark)` : `Current: ${theme}`;
        document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
            btn.title = label;
            btn.setAttribute('aria-label', label);
        });
    }

    toggle() {
        const current = document.documentElement.dataset.theme || 'light';
        this.setTheme(current === 'dark' ? 'light' : 'dark');
    }

    bindToggleButtons() {
        document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.toggle();
            });
        });
    }

    buildThemeDropdowns() {
        const current = document.documentElement.dataset.theme || 'light';
        document.querySelectorAll('.theme-select').forEach((sel) => {
            if (sel.options.length === 0) {
                this.availableThemes.forEach((t) => {
                    const opt = document.createElement('option');
                    opt.value = t.id;
                    opt.textContent = t.label;
                    sel.appendChild(opt);
                });
            }
            sel.value = current;
            sel.addEventListener('change', () => {
                this.setTheme(sel.value);
            });
        });
    }
}

function initThemeManager() {
    document.documentElement.classList.add('theme-ready');
    document.documentElement.classList.remove('theme-early');
    globalThis.themeManager = new ThemeManager();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initThemeManager);
} else {
    initThemeManager();
}
