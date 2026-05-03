// Theme Manager - handles multi-theme switching with cookie persistence
// DaisyUI themes are activated via data-theme on <html>

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
        } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            this.setTheme('dark', false);
        } else {
            this.setTheme('light', false);
        }
        this.bindToggleButtons();
        this.buildThemeDropdowns();
        this.bindThemeSelectorLinks();
    }

    bindThemeSelectorLinks() {
        const self = this;
        // Bind to any element with data-set-theme (dropdown items, etc.)
        document.querySelectorAll('[data-set-theme]').forEach(el => {
            el.addEventListener('click', function(e) {
                e.preventDefault();
                const theme = this.getAttribute('data-set-theme');
                console.log('Switching to theme:', theme);
                self.setTheme(theme);
            });
        });
    }

    getSavedTheme() {
        var match = new RegExp('(?:^|; )' + this.storageKey + '=([^;]*)').exec(document.cookie);
        return match ? decodeURIComponent(match[1]) : null;
    }

    saveTheme(theme) {
        var expires = new Date(Date.now() + 365 * 864e5).toUTCString();
        document.cookie = this.storageKey + '=' + encodeURIComponent(theme) +
            '; expires=' + expires + '; path=/; SameSite=Lax';
    }

    setTheme(theme, save) {
        document.documentElement.setAttribute('data-theme', theme);
        if (save !== false) {
            this.saveTheme(theme);
        }
        // Update all theme selector dropdowns to reflect current theme
        document.querySelectorAll('.theme-select').forEach(function(sel) {
            sel.value = theme;
        });
        // Update toggle button accessibility labels
        var isDark = this.availableThemes.find(function(t) { return t.id === theme; });
        var label = isDark && isDark.dark ? 'Current: ' + theme + ' (dark)' : 'Current: ' + theme;
        document.querySelectorAll('.theme-toggle-btn').forEach(function(btn) {
            btn.title = label;
            btn.setAttribute('aria-label', label);
        });
    }

    toggle() {
        var current = document.documentElement.getAttribute('data-theme') || 'light';
        this.setTheme(current === 'dark' ? 'light' : 'dark');
    }

    bindToggleButtons() {
        var self = this;
        document.querySelectorAll('.theme-toggle-btn').forEach(function(btn) {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                self.toggle();
            });
        });
    }

    buildThemeDropdowns() {
        var self = this;
        var current = document.documentElement.getAttribute('data-theme') || 'light';
        document.querySelectorAll('.theme-select').forEach(function(sel) {
            // Only populate if empty (avoid duplicating on re-init)
            if (sel.options.length === 0) {
                self.availableThemes.forEach(function(t) {
                    var opt = document.createElement('option');
                    opt.value = t.id;
                    opt.textContent = t.label;
                    sel.appendChild(opt);
                });
            }
            sel.value = current;
            sel.addEventListener('change', function() {
                self.setTheme(this.value);
            });
        });
    }
}

function initThemeManager() {
    globalThis.themeManager = new ThemeManager();
}

// Support both standard page loads and cases where script executes after DOM ready.
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initThemeManager);
} else {
    initThemeManager();
}
