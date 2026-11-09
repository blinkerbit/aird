import { initListsHub } from './lists-hub.js';

const root = document.querySelector('[data-hub="lists"]');
if (root) initListsHub(root);
