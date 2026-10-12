import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      '/static/js': path.join(root, 'aird/static/js'),
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['tests/js/**/*.test.js'],
    restoreMocks: true,
    clearMocks: true,
  },
});
