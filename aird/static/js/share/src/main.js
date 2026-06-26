/**
 * Share page entry — edit modules in this directory, not ../app.js (esbuild bundle).
 *
 * Modules:
 *   state.js, utils.js, selection.js, expiry.js, file-icons.js
 *   file-picker.js, cloud.js, create-share.js, create-users.js
 *   shares-list.js, share-popup.js, add-files-modal.js
 *   management.js, management-templates.js, init.js (wiring)
 *
 * Build: npm run js:share  |  Watch: npm run js:share:watch
 */
import { initSharePage } from './init.js';

initSharePage();
