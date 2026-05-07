import globals from "globals";
import sonarjs from "eslint-plugin-sonarjs";

/** Browser classic scripts under static/js (no ES modules). */
export default [
  {
    ignores: ["node_modules/**", "aird/static/css/**"],
  },
  {
    ...sonarjs.configs.recommended,
    files: ["aird/static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        ...globals.es2021,
      },
    },
    rules: {
      ...sonarjs.configs.recommended.rules,
      "no-console": "off",
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
];
