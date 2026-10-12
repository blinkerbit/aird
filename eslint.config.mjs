import globals from "globals";
import sonarjs from "eslint-plugin-sonarjs";

/** ES-module sources under static/js; everything else is a classic script. */
export default [
  {
    ignores: [
      "node_modules/**",
      "aird/static/css/**",
      "aird/static/js/vendor/**",
      "aird/static/js/share/app.js",
      "aird/static/js/browse/app.bundle.js",
      "aird/static/js/pages/dist/**",
    ],
  },
  {
    ...sonarjs.configs.recommended,
    files: [
      "aird/static/js/share/src/**/*.js",
      "aird/static/js/core/**/*.js",
      "aird/static/js/browse/**/*.js",
      "aird/static/js/onedrive-fe/**/*.js",
      "aird/static/js/chat-attach.js",
      "aird/static/js/gitlab.js",
      "aird/static/js/gitlab-boot.js",
      "aird/static/js/onedrive-browser.js",
      "aird/static/js/pages/chat.js",
      "aird/static/js/pages/p2p-page.js",
      "aird/static/js/pages/super-search.js",
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.es2021,
        AirdCore: "readonly",
      },
    },
    rules: {
      ...sonarjs.configs.recommended.rules,
      "no-console": "off",
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-undef": "error",
    },
  },
  {
    ...sonarjs.configs.recommended,
    files: ["aird/static/js/**/*.js"],
    ignores: [
      "aird/static/js/share/src/**",
      "aird/static/js/core/**",
      "aird/static/js/browse/**",
      "aird/static/js/onedrive-fe/**",
      "aird/static/js/chat-attach.js",
      "aird/static/js/gitlab.js",
      "aird/static/js/gitlab-boot.js",
      "aird/static/js/onedrive-browser.js",
      "aird/static/js/pages/chat.js",
      "aird/static/js/pages/p2p-page.js",
      "aird/static/js/pages/super-search.js",
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        ...globals.es2021,
        AirdQRCode: "readonly",
        AirdCore: "readonly",
        AirdFolderPicker: "readonly",
      },
    },
    rules: {
      ...sonarjs.configs.recommended.rules,
      "no-console": "off",
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-undef": "error",
    },
  },
];
