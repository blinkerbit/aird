class SuperSearch {
      ws = null;
      isSearching = false;
      cancelled = false;
      results = new Map(); // file_path -> {matches: [], collapsed: false}
      totalMatches = 0;
      totalFiles = 0;
      processedFiles = 0;

      constructor() {
        this.initializeElements();
        this.bindEvents();
      }

      initializeElements() {
        this.patternInput = document.getElementById('pattern');
        this.searchTextInput = document.getElementById('searchText');
        this.searchBtn = document.getElementById('searchBtn');
        this.cancelBtn = document.getElementById('cancelBtn');
        this.clearBtn = document.getElementById('clearBtn');
        this.statusDiv = document.getElementById('status');
        this.statusText = document.getElementById('statusText');
        this.statusIcon = this.statusDiv.querySelector('svg');
        this.progressContainer = document.getElementById('progressContainer');
        this.progressFill = document.getElementById('progressFill');
        this.progressText = document.getElementById('progressText');
        this.scanningTicker = document.getElementById('scanningTicker');
        this.resultsDiv = document.getElementById('results');
        this.resultsContent = document.getElementById('resultsContent');
        this.statsDiv = document.getElementById('stats');

        this.patternHint = document.getElementById('patternHint');
        this.patternHelp = document.getElementById('patternHelp');

        // Search mode elements
        this.searchModeRadios = document.querySelectorAll('input[name="searchMode"]');
        this.searchTextLabel = document.getElementById('searchTextLabel');
        this.searchTextHelp = document.getElementById('searchTextHelp');
        this.modeDescription = document.getElementById('modeDescription');

        // Set platform-specific defaults
        this.setupPlatformDefaults();
      }

      setVisible(el, visible) {
        if (!el) return;
        el.classList.toggle('hidden', !visible);
        if (visible) el.style.display = '';
        else if (el.style) el.style.display = '';
      }

      setupPlatformDefaults() {
        // Prefer Client Hints platform when available (navigator.userAgent is deprecated)
        const plat = navigator.userAgentData?.platform;
        const isWindows =
          typeof plat === 'string' && plat.length > 0
            ? /windows/i.test(plat)
            : /windows|win32/i.test(navigator.userAgent);
        const separator = isWindows ? '\\' : '/';

        const currentPath = document.body?.dataset.searchBasePath ?? '';

        // Build default pattern based on current path
        let defaultPattern;
        if (currentPath && currentPath.trim() !== '') {
          const normalizedPath = currentPath.replaceAll(/[/\\]/g, separator);
          defaultPattern = `${normalizedPath}${separator}**${separator}*.txt`;
        } else {
          defaultPattern = isWindows ? String.raw`**\*.txt` : '**/*.txt';
        }

        if (isWindows) {
          this.patternInput.placeholder = String.raw`e.g., *.py, **\*.txt, src\**\*.js`;
        } else {
          this.patternInput.placeholder = 'e.g., *.py, **/*.txt, src/**/*.js';
        }

        this.patternInput.value = defaultPattern;
        if (this.patternHelp) {
          const sep = this.escapeHtml(separator);
          const note = this.patternHelp.querySelector('.ss-sep-note');
          if (note) {
            note.innerHTML = `Path separator: <code>${sep}</code>. Searches from your root directory.`;
          }
        }
        this.validatePattern();
      }

      bindEvents() {
        this.searchBtn.addEventListener('click', () => this.startSearch());
        this.cancelBtn.addEventListener('click', () => this.cancelSearch());
        this.clearBtn.addEventListener('click', () => this.clearResults());

        // Allow Enter key to start search
        this.patternInput.addEventListener('keypress', (e) => {
          if (e.key === 'Enter' && !this.isSearching) this.startSearch();
        });
        this.searchTextInput.addEventListener('keypress', (e) => {
          if (e.key === 'Enter' && !this.isSearching) this.startSearch();
        });

        // Pattern button clicks
        document.querySelectorAll('.pattern-btn').forEach(btn => {
          btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const pattern = btn.dataset.pattern;
            this.patternInput.value = pattern;
            this.patternInput.focus();
            this.validatePattern();
          });
        });

        // Pattern validation on input
        this.patternInput.addEventListener('input', () => this.validatePattern());

        // Search mode toggle
        this.searchModeRadios.forEach(radio => {
          radio.addEventListener('change', () => this.updateSearchMode());
        });
      }

      updateSearchMode() {
        const selectedMode = document.querySelector('input[name="searchMode"]:checked').value;

        if (selectedMode === 'filename') {
          this.searchTextLabel.textContent = 'Filename to Search:';
          this.searchTextInput.placeholder = 'Enter filename or part of filename to search for';
          this.searchTextHelp.innerHTML = 'Search for files by name. Use wildcards like <code>*</code> and <code>?</code> for pattern matching.';
          this.modeDescription.textContent = 'Search for files by filename within the specified pattern';
        } else {
          this.searchTextLabel.textContent = 'Search Text:';
          this.searchTextInput.placeholder = 'Text to search for in matching files';
          this.searchTextHelp.innerHTML = 'Case-sensitive text search within matching files';
          this.modeDescription.textContent = 'Search for text within file contents';
        }
      }

      validatePattern() {
        const pattern = this.patternInput.value.trim();
        const hint = this.patternHint;
        if (!hint) return;

        hint.classList.remove('is-ok', 'is-warn', 'is-err');

        if (!pattern) {
          hint.textContent = 'Enter a glob pattern to match files.';
          return;
        }

        const hasWildcard = pattern.includes('*') || pattern.includes('?') || pattern.includes('[');
        const isValid = pattern.length > 0 && !pattern.includes('//');

        if (isValid) {
          if (hasWildcard) {
            hint.classList.add('is-ok');
            hint.textContent = 'Wildcard pattern ready.';
          } else {
            hint.classList.add('is-warn');
            hint.textContent = 'Literal name match (no wildcards).';
          }
        } else {
          hint.classList.add('is-err');
          hint.textContent = 'Invalid pattern — check for empty segments or double slashes.';
        }
      }

      startSearch() {
        const pattern = this.patternInput.value.trim();
        const searchText = this.searchTextInput.value.trim();
        const searchMode = document.querySelector('input[name="searchMode"]:checked').value;

        if (!pattern || !searchText) {
          this.showStatus('Please enter both file pattern and search text.', 'error');
          return;
        }

        this.isSearching = true;
        this.cancelled = false;
        this.updateButtons();
        this.clearResults();
        this.showStatus('Connecting to search service...', 'searching');

        // Create WebSocket connection
        const protocol = globalThis.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${globalThis.location.host}/search/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
          this.showStatus('Connected. Starting search...', 'searching');
          this.ws.send(JSON.stringify({
            pattern: pattern,
            search_text: searchText,
            search_mode: searchMode
          }));
        };

        this.ws.onmessage = (event) => {
          let payload;
          try {
            payload = JSON.parse(event.data);
          } catch (err) {
            console.warn('Malformed search message:', err);
            this.showStatus('Received a malformed message from the search service.', 'error');
            return;
          }
          try {
            this.handleMessage(payload);
          } catch (err) {
            console.error('Error handling search message:', err);
            this.showStatus('Error while processing search results.', 'error');
            this.isSearching = false;
            this.updateButtons();
          }
        };

        this.ws.onclose = (event) => {
          this.isSearching = false;
          this.updateButtons();

          // Check if closed due to authentication failure (code 1008)
          if (event.code === 1008) {
            this.showStatus('Authentication required. Redirecting to login...', 'error');
            setTimeout(() => {
              globalThis.location.href = '/login?next=' + encodeURIComponent(globalThis.location.pathname);
            }, 1500);
            return;
          }

          // Don't overwrite status if user explicitly cancelled
          if (this.cancelled) return;

          if (this.totalMatches === 0 && this.processedFiles > 0) {
            this.showStatus('Search completed - no matches found.', 'success');
          }
        };

        this.ws.onerror = () => {
          this.showStatus('Connection error. Please try again.', 'error');
          this.isSearching = false;
          this.updateButtons();
        };
      }

      handleMessage(data) {
        switch (data.type) {
          case 'auth_required': {
            // Authentication required - redirect to login page
            this.showStatus('Authentication required. Redirecting to login...', 'error');
            this.isSearching = false;
            this.updateButtons();
            this.setVisible(this.progressContainer, false);
            // Redirect to login page with next parameter
            const redirectUrl = data.redirect || '/login?next=' + encodeURIComponent(globalThis.location.pathname);
            setTimeout(() => {
              globalThis.location.href = redirectUrl;
            }, 1500); // Give user time to see the message
            break;
          }

          case 'search_start':
            if (data.search_mode === 'filename') {
              this.showStatus(`Searching for filenames matching "${data.search_text}" in pattern "${data.pattern}"...`, 'searching');
            } else {
              this.showStatus(`Searching for "${data.search_text}" in files matching "${data.pattern}"...`, 'searching');
            }
            this.setVisible(this.progressContainer, true);
            this.scanningTicker.textContent = '';
            break;

          case 'scanning':
            this.processedFiles = data.files_searched;
            this.scanningTicker.textContent = data.file_path;
            this.progressText.textContent = `${data.files_searched} files scanned`;
            break;

          case 'match':
            this.addMatch(data);
            break;

          case 'search_complete':
            this.showStatus(`Search completed — ${this.totalMatches} matches in ${this.results.size} files.`, 'success');
            this.progressFill.value = 100;
            this.progressText.textContent = 'Done';
            this.progressFill.classList.remove('progress-primary');
            this.progressFill.classList.add('progress-success');
            this.scanningTicker.textContent = '';
            this.isSearching = false;
            this.updateButtons();
            break;

          case 'no_files':
            this.showStatus(data.message || `No matches found (${data.files_searched} files scanned).`, 'success');
            this.setVisible(this.progressContainer, false);
            this.scanningTicker.textContent = '';
            this.isSearching = false;
            this.updateButtons();
            break;

          case 'cancelled':
            this.showStatus('Search cancelled.', 'error');
            this.setVisible(this.progressContainer, false);
            this.scanningTicker.textContent = '';
            this.isSearching = false;
            this.updateButtons();
            break;

          case 'error':
          case 'file_error':
            this.showStatus(`Error: ${data.message}`, 'error');
            if (data.type === 'error') {
              this.isSearching = false;
              this.updateButtons();
              this.setVisible(this.progressContainer, false);
            }
            break;
        }
      }

      addMatch(data) {
        const filePath = data.file_path;

        if (!this.results.has(filePath)) {
          this.results.set(filePath, { matches: [], collapsed: false });
          this.createFileSection(filePath);
        }

        const fileData = this.results.get(filePath);
        fileData.matches.push(data);
        this.totalMatches++;

        this.addMatchToDOM(filePath, data);
        this.updateStats();
      }

      createFileSection(filePath) {
        const section = document.createElement('div');
        section.className = 'ss-file';
        section.id = `file-${this.escapeId(filePath)}`;

        const header = document.createElement('div');
        header.className = 'ss-file-header';

        const pathSegments = filePath.split(/[/\\]/);
        const fileName = pathSegments[pathSegments.length - 1];
        const dirName = pathSegments.slice(0, -1).join('/') || '/';

        const dirHref = '/files/' + encodeURIComponent(dirName.replace(/^\/+/, ''));
        const fileHref = '/files/' + filePath.replace(/^\/+/, '');
        header.innerHTML = `
            <div class="file-path flex items-center gap-1.5 text-base-content font-semibold text-sm flex-1 min-w-0 overflow-hidden">
              <svg class="w-4 h-4 opacity-45 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>
              <a href="${dirHref}" target="_blank" rel="noopener noreferrer"
                 class="opacity-45 truncate hidden sm:inline hover:opacity-100 hover:text-primary transition-opacity no-underline hover:underline shrink min-w-0"
                 title="Open folder">${this.escapeHtml(dirName)}/</a><a href="${fileHref}" target="_blank" rel="noopener noreferrer"
                 class="font-bold text-primary hover:underline shrink-0 no-underline"
                 title="Open file">${this.escapeHtml(fileName)}</a>
            </div>
            <button type="button" class="file-toggle btn btn-xs btn-ghost btn-circle shrink-0 opacity-55" title="Collapse file results" aria-label="Collapse">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
            </button>
          `;

        header.addEventListener('click', (e) => {
          if (e.target.closest('a')) { return; }
          this.toggleFileSection(filePath);
        });

        const matchList = document.createElement('div');
        matchList.className = 'ss-match-list';
        matchList.id = `matches-${this.escapeId(filePath)}`;

        section.appendChild(header);
        section.appendChild(matchList);

        this.resultsContent.appendChild(section);
        this.setVisible(this.resultsDiv, true);
        const hint = document.getElementById('idleHint');
        this.setVisible(hint, false);
      }

      addMatchToDOM(filePath, matchData) {
        const matchList = document.getElementById(`matches-${this.escapeId(filePath)}`);
        const matchItem = document.createElement('div');
        matchItem.className = 'ss-match';

        const highlightedContent = this.highlightMatches(matchData.line_content, matchData.search_text, matchData.match_positions);

        if (matchData.line_number === 0) {
          matchItem.innerHTML = `
              <span class="ss-match-line">file</span>
              <span class="ss-match-text text-primary font-semibold">${highlightedContent}</span>
            `;
        } else {
          const lineNum = Number(matchData.line_number);
          const lineLabel = Number.isFinite(lineNum) ? String(lineNum) : this.escapeHtml(String(matchData.line_number ?? ''));
          matchItem.innerHTML = `
              <span class="ss-match-line">${lineLabel}</span>
              <span class="ss-match-text">${highlightedContent}</span>
            `;
        }

        matchList.appendChild(matchItem);
      }

      highlightMatches(content, searchText, positions) {
        if (!positions || positions.length === 0) {
          return this.escapeHtml(content);
        }

        let result = '';
        let lastIndex = 0;

        positions.forEach(pos => {
          result += this.escapeHtml(content.substring(lastIndex, pos));
          result += `<span class="ss-hit">${this.escapeHtml(content.substring(pos, pos + searchText.length))}</span>`;
          lastIndex = pos + searchText.length;
        });

        result += this.escapeHtml(content.substring(lastIndex));
        return result;
      }

      toggleFileSection(filePath) {
        const fileData = this.results.get(filePath);
        const matchList = document.getElementById(`matches-${this.escapeId(filePath)}`);
        const toggle = document.querySelector(`#file-${this.escapeId(filePath)} .file-toggle`);

        fileData.collapsed = !fileData.collapsed;

        if (fileData.collapsed) {
          matchList.classList.add('hidden');
          toggle.classList.add('-rotate-90');
        } else {
          matchList.classList.remove('hidden');
          toggle.classList.remove('-rotate-90');
        }
      }

      updateProgress() {
        this.progressText.textContent = `${this.processedFiles} files scanned`;
      }

      updateStats() {
        this.statsDiv.textContent =
          `${this.totalMatches} match${this.totalMatches === 1 ? '' : 'es'} · ${this.results.size} file${this.results.size === 1 ? '' : 's'}`;
      }

      cancelSearch() {
        this.cancelled = true;
        if (this.ws) {
          this.ws.close();
        }
        this.isSearching = false;
        this.updateButtons();
        this.showStatus('Search cancelled.', 'error');
        this.setVisible(this.progressContainer, false);
        this.scanningTicker.textContent = '';
      }

      clearResults() {
        this.results.clear();
        this.totalMatches = 0;
        this.totalFiles = 0;
        this.processedFiles = 0;
        this.resultsContent.innerHTML = '';
        this.statsDiv.textContent = '';
        this.setVisible(this.resultsDiv, false);
        this.setVisible(this.progressContainer, false);
        this.progressFill.value = 0;
        this.progressFill.classList.remove('progress-success');
        this.progressFill.classList.add('progress-primary');
        this.scanningTicker.textContent = '';
        const hint = document.getElementById('idleHint');
        this.setVisible(hint, true);
      }

      updateButtons() {
        this.searchBtn.disabled = this.isSearching;
        this.cancelBtn.disabled = !this.isSearching;
        this.patternInput.disabled = this.isSearching;
        this.searchTextInput.disabled = this.isSearching;
      }

      showStatus(message, type = 'info') {
        if (!message) {
          this.statusDiv.classList.add('hidden');
          return;
        }

        this.statusDiv.classList.remove('hidden');
        this.statusText.textContent = message;

        // Reset classes
        this.statusDiv.className = 'alert mt-5';

        // Map status types to DaisyUI alert classes
        const typeMap = {
          'info': 'alert-info',
          'searching': 'alert-info',
          'success': 'alert-success',
          'error': 'alert-error',
          'warning': 'alert-warning'
        };

        const alertClass = typeMap[type] || 'alert-info';
        this.statusDiv.classList.add(alertClass);

        // Update icon based on type
        if (type === 'searching') {
          this.statusIcon.innerHTML = '<path fill="currentColor" d="M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="1s" repeatCount="indefinite"/></path>';
        } else if (type === 'success') {
          this.statusIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />';
        } else if (type === 'error') {
          this.statusIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />';
        } else {
          this.statusIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>';
        }
      }

      escapeHtml(text) {
        return globalThis.AirdCore.escapeHtml(text);
      }

      escapeId(text) {
        return text.replaceAll(/[^a-zA-Z0-9]/g, '_');
      }
    }

    // Initialize the search interface when the page loads
    document.addEventListener('DOMContentLoaded', () => {
      globalThis.superSearch = new SuperSearch();
    });
