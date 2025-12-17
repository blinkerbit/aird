// Theme Manager
class ThemeManager {
    constructor() {
        this.init();
    }

    init() {
        console.log("Theme initialized");
        // Future: Add dark/light mode toggle logic here
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.themeManager = new ThemeManager();
});
