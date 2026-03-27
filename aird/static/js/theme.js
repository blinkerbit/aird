// Theme Manager - handles dark/light mode toggle with cookie persistence
class ThemeManager {
    get storageKey() {
        return 'aird_theme';
    }

    constructor() {
        this.init();
    }

    init() {
        const saved = this.getSavedTheme();
        if (saved) {
            this.setTheme(saved, false);
        } else {
            // Ensure a deterministic initial state and correct icon/title.
            this.setTheme('light', false);
        }
        this.bindToggleButtons();
    }

    getSavedTheme() {
        // Read from cookie
        const match = new RegExp('(?:^|; )' + this.storageKey + '=([^;]*)').exec(document.cookie);
        return match ? decodeURIComponent(match[1]) : null;
    }

    saveTheme(theme) {
        const expires = new Date(Date.now() + 365 * 864e5).toUTCString();
        document.cookie = this.storageKey + '=' + encodeURIComponent(theme) + '; expires=' + expires + '; path=/; SameSite=Lax';
    }

    setTheme(theme, save) {
        document.documentElement.dataset.theme = theme;
        if (save !== false) {
            this.saveTheme(theme);
        }
        // Update all toggle button icons
        document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
            btn.textContent = theme === 'dark' ? '\u2600\uFE0F' : '\uD83C\uDF19';
            btn.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
        });
    }

    toggle() {
        const current = document.documentElement.dataset.theme;
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
