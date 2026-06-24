import globals from "globals";
import sonarjs from "eslint-plugin-sonarjs";

/** Browser classic scripts under static/js (no ES modules). */
export default [
  {
    ignores: [
      "node_modules/**",
      "aird/static/css/**",
      "aird/static/js/vendor/**",
      "aird/static/js/share/app.js",
    ],
  },
  {
    ...sonarjs.configs.recommended,
    files: ["aird/static/js/share/src/**/*.js"],
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
    ignores: ["aird/static/js/share/src/**"],
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
