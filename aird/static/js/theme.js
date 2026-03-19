// Theme Manager - handles dark/light mode toggle with cookie persistence
class ThemeManager {
    constructor() {
        this.storageKey = 'aird_theme';
        this.init();
    }

    init() {
        const saved = this.getSavedTheme();
        if (saved) {
            this.setTheme(saved, false);
        }
        this.bindToggleButtons();
    }

    getSavedTheme() {
        // Read from cookie
        const match = document.cookie.match(new RegExp('(?:^|; )' + this.storageKey + '=([^;]*)'));
        return match ? decodeURIComponent(match[1]) : null;
    }

    saveTheme(theme) {
        const expires = new Date(Date.now() + 365 * 864e5).toUTCString();
        document.cookie = this.storageKey + '=' + encodeURIComponent(theme) + '; expires=' + expires + '; path=/; SameSite=Lax';
    }

    setTheme(theme, save) {
        document.documentElement.setAttribute('data-theme', theme);
        if (save !== false) {
            this.saveTheme(theme);
        }
        // Update all toggle button icons
        document.querySelectorAll('.theme-toggle-btn').forEach(function(btn) {
            btn.textContent = theme === 'dark' ? '\u2600\uFE0F' : '\uD83C\uDF19';
            btn.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
        });
    }

    toggle() {
        var current = document.documentElement.getAttribute('data-theme');
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
}

document.addEventListener('DOMContentLoaded', function() {
    globalThis.themeManager = new ThemeManager();
});
