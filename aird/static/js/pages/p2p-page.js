let isAnonymous = document.body.dataset.isAnonymous === 'true';

        // =====================================================================
        // P2P share QR — node-qrcode (MIT), bundled as AirdQRCode in vendor/qrcode-browser.js
        // =====================================================================
        function renderP2pShareQrOnCanvas(canvas, text) {
            return new Promise((resolve) => {
                if (!text || !canvas || !globalThis.AirdQRCode?.toCanvas) {
                    resolve(false);
                    return;
                }
                globalThis.AirdQRCode.toCanvas(canvas, text, {
                    errorCorrectionLevel: 'M',
                    margin: 3,
                    width: 288,
                    color: { dark: '#000000', light: '#FFFFFF' },
                }, (err) => {
                    resolve(!err);
                });
            });
        }

        // =====================================================================
        // P2P Transfer Application
        // =====================================================================

        function buildP2pShareJoinUrl(roomId) {
            const id = typeof roomId === 'string' ? roomId.trim() : '';
            if (!id) return '';
            return `${globalThis.location.origin}/p2p?room=${encodeURIComponent(id)}`;
        }

        // Configuration
        const CHUNK_SIZE = 16384; // 16KB chunks for WebRTC

        // Define available STUN server options
        const STUN_OPTIONS = {
            'google': [
                { urls: 'stun:stun.l.google.com:19302' },
                { urls: 'stun:stun1.l.google.com:19302' },
                { urls: 'stun:stun2.l.google.com:19302' },
                { urls: 'stun:stun3.l.google.com:19302' },
                { urls: 'stun:stun4.l.google.com:19302' }
            ],
            'mozilla': [
                { urls: 'stun:stun.services.mozilla.com' }
            ],
            'openstun': [
                { urls: 'stun:stun.ekiga.net' }
            ],
            'blackberry': [
                { urls: 'stun:stun.voip.blackberry.com:3478' }
            ],
            'framasoft': [
                { urls: 'stun:stun.framasoft.org' }
            ]
        };

        // Default to Google
        let currentIceServers = STUN_OPTIONS['google'];

        // State
        let ws = null;
        let peerConnection = null;
        let dataChannel = null;
        let selectedFiles = [];
        let currentFileIndex = 0;
        let currentMode = null;
        let _myPeerId = null;
        let currentRoomId = null;
        let receivedChunks = [];
        let receivedSize = 0;
        let expectedFileInfo = null;
        let transferStartTime = null;
        let availableFiles = [];
        let requestedFiles = new Set();
        let pendingRequests = [];
        let isTransferring = false;
        let otherPeerInRoom = false;
        const pendingRoomIdRaw = document.body.dataset.pendingRoomId || '';
        let pendingRoomId = pendingRoomIdRaw || null;
        let receivedFiles = []; // Array to store received files for manual download
        const p2pPatternKit = globalThis.P2PPatterns?.createP2PPatternKit({
            currentMode: null,
            currentRoomId: null,
            isTransferring: false,
            otherPeerInRoom: false
        });
        const p2pStateMachine = p2pPatternKit?.stateMachine || null;
        const p2pMediator = p2pPatternKit?.mediator || null;

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            initWebSocket();
            setupFileInput();
            setupDropZone();

            // Check for room ID in URL or if anonymous user with pending room
            const urlParams = new URLSearchParams(globalThis.location.search);
            const roomId = urlParams.get('room');
            if (roomId || pendingRoomId) {
                selectMode('receive');
                document.getElementById('room-code-input').value = roomId || pendingRoomId;
                // For anonymous users, the auto-join happens after WebSocket connects
            }

            // Warn user before leaving if transfer is active (standard BeforeUnloadEvent; avoid legacy window.event / returnValue text)
            globalThis.addEventListener('beforeunload', (event) => {
                const transferActive =
                    peerConnection?.connectionState === 'connected' ||
                    (receivedChunks.length > 0 &&
                        receivedChunks.length * CHUNK_SIZE < (expectedFileInfo?.size ?? 0));
                if (transferActive) {
                    event.preventDefault();
                }
            });

            // Handle STUN server selection change
            const stunSelect = document.getElementById('stun-server-select');
            if (stunSelect) {
                stunSelect.addEventListener('change', (e) => {
                    const selected = e.target.value;
                    if (STUN_OPTIONS[selected]) {
                        currentIceServers = STUN_OPTIONS[selected];
                        log(`STUN server changed to: ${selected}`, 'info');
                    }
                });
            }

            document.getElementById('apply-reconnect-btn')?.addEventListener('click', restartConnection);
        });

        function updateApplyReconnectButton() {
            const btn = document.getElementById('apply-reconnect-btn');
            if (!btn) return;
            btn.disabled = !(currentRoomId && otherPeerInRoom && ws?.readyState === 1);
        }

        function restartConnection() {
            if (!currentRoomId || !otherPeerInRoom || ws?.readyState !== 1) return;
            const btn = document.getElementById('apply-reconnect-btn');
            if (btn) btn.disabled = true;
            log('Applying new STUN server and reconnecting...', 'info');
            closePeerConnection();
            ws.send(JSON.stringify({ type: 'restart_connection' }));
            if (currentMode === 'send') {
                createPeerConnection(true);
            }
            setTimeout(updateApplyReconnectButton, 2000);
        }

        function initWebSocket() {
            const protocol = globalThis.location.protocol === 'https:' ? 'wss:' : 'ws:';
            let wsUrl = `${protocol}//${globalThis.location.host}/p2p/signal`;

            // For anonymous users, pass the room ID in the WebSocket URL
            if (isAnonymous && pendingRoomId) {
                wsUrl += `?room=${encodeURIComponent(pendingRoomId)}`;
            }

            log(`Connecting to ${wsUrl}...`, 'info');

            try {
                ws = new WebSocket(wsUrl);
            } catch (e) {
                log(`Failed to create WebSocket: ${e.message}`, 'error');
                return;
            }

            ws.onopen = () => {
                log('Connected to signaling server', 'success');
            };

            ws.onclose = (event) => {
                log(`Disconnected from signaling server (code: ${event.code}, reason: ${event.reason || 'none'})`, 'error');
                // Only reconnect if not a deliberate close or auth failure
                if (event.code !== 1000 && event.code !== 1008) {
                    setTimeout(initWebSocket, 3000);
                } else if (event.code === 1008 && isAnonymous) {
                    log('This share link requires login. Please log in to receive the file.', 'error');
                }
            };

            ws.onerror = (error) => {
                log('WebSocket connection error - check if server is running', 'error');
                console.error('WebSocket error:', error);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    handleSignalingMessage(data);
                } catch (e) {
                    log(`Error parsing message: ${e.message}`, 'error');
                }
            };
        }

        function handleConnectedMessage(data) {
            _myPeerId = data.peer_id;
            isAnonymous = data.is_anonymous || false;
            log(`Connected as ${data.username}${data.is_anonymous ? ' (guest)' : ''} (${data.peer_id.substring(0, 8)}...)`, 'info');
            if (!data.pending_room || !isAnonymous) return;
            log(`Auto-joining room ${data.pending_room}...`, 'info');
            setTimeout(() => {
                document.getElementById('room-code-input').value = data.pending_room;
                joinRoom();
            }, 500);
        }

        function handleRoomCreatedMessage(data) {
            currentRoomId = data.room_id;
            document.getElementById('share-code').textContent = data.room_id;
            document.getElementById('share-code-container').classList.remove('hidden');
            const anonHint = document.getElementById('share-code-hint');
            anonHint.innerHTML = data.allow_anonymous
                ? 'Share this code with the recipient<br><span class="sq-style-a5e3c2">✓ Anonymous access enabled - no login required</span>'
                : 'Share this code with the recipient<br><span class="sq-style-28e6a7">Recipients must be logged in</span>';
            generateShareQRCode(data.room_id);
            updateSendStatus('waiting', 'Waiting for recipient to connect...');
            log(`Room created: ${data.room_id}${data.allow_anonymous ? ' (anonymous access enabled)' : ''}`, 'success');
            updateApplyReconnectButton();
        }

        function handleRoomJoinedMessage(data) {
            currentRoomId = data.room_id;
            otherPeerInRoom = (data.peer_count || 0) >= 2;
            if (data.file_info) {
                expectedFileInfo = data.file_info;
                showReceiveFileInfo(data.file_info);
            }
            updateReceiveStatus('waiting', 'Connected! Waiting for sender...');
            log(`Joined room: ${data.room_id}`, 'success');
            updateApplyReconnectButton();
        }

        function handlePeerJoinedMessage(data) {
            otherPeerInRoom = true;
            log(`Peer joined: ${data.username}`, 'success');
            updateSendStatus('connected', `Connected to ${data.username}`);
            if (currentMode === 'send') {
                createPeerConnection(true);
            }
            document.getElementById('session-warning').style.display = 'flex';
            updateApplyReconnectButton();
        }

        function handlePeerLeftMessage(data) {
            otherPeerInRoom = false;
            log(`Peer left: ${data.username}`, 'info');
            closePeerConnection();
            document.getElementById('session-warning').style.display = 'none';
            updateApplyReconnectButton();
        }

        function handleRestartConnectionMessage(data) {
            log(`Reconnecting with new settings (requested by ${data.username || 'peer'})...`, 'info');
            closePeerConnection();
            if (currentMode === 'send') {
                createPeerConnection(true);
            }
        }

        function handleErrorMessage(data) {
            log(`Error: ${data.message}`, 'error');
            document.getElementById('create-room-btn').disabled = false;
            document.getElementById('join-room-btn').disabled = false;
        }

        const SIGNALING_MESSAGE_HANDLERS = {
            connected: handleConnectedMessage,
            room_created: handleRoomCreatedMessage,
            room_joined: handleRoomJoinedMessage,
            peer_joined: handlePeerJoinedMessage,
            peer_left: handlePeerLeftMessage,
            restart_connection: handleRestartConnectionMessage,
            offer: (data) => {
                log('Received connection offer', 'info');
                handleOffer(data.sdp);
            },
            answer: (data) => {
                log('Received connection answer', 'info');
                handleAnswer(data.sdp);
            },
            ice_candidate: (data) => {
                handleIceCandidate(data.candidate);
            },
            file_info_updated: (data) => {
                expectedFileInfo = data.file_info;
                showReceiveFileInfo(data.file_info);
            },
            error: handleErrorMessage
        };

        function handleSignalingMessage(data) {
            const SignalingServiceCtor = globalThis.P2PPatterns?.SignalingService;
            if (!SignalingServiceCtor) {
                const handler = SIGNALING_MESSAGE_HANDLERS[data.type];
                if (handler) handler(data);
                return;
            }
            if (!handleSignalingMessage._service) {
                handleSignalingMessage._service = new SignalingServiceCtor(
                    SIGNALING_MESSAGE_HANDLERS
                );
            }
            handleSignalingMessage._service.dispatch(data);
            p2pMediator?.emit("signaling:message", data);
        }

        function selectMode(mode) {
            currentMode = mode;
            p2pStateMachine?.transition("MODE_SELECTED", { mode });
            document.getElementById('mode-section').classList.add('hidden');

            if (mode === 'send') {
                document.getElementById('send-section').classList.remove('hidden');
                document.getElementById('receive-section').classList.add('hidden');
                document.getElementById('send-mode-btn').classList.add('selected');
                document.getElementById('receive-mode-btn').classList.remove('selected');
            } else {
                document.getElementById('receive-section').classList.remove('hidden');
                document.getElementById('send-section').classList.add('hidden');
                document.getElementById('receive-mode-btn').classList.add('selected');
                document.getElementById('send-mode-btn').classList.remove('selected');
            }
        }

        function setupFileInput() {
            const fileInput = document.getElementById('file-input');
            fileInput.addEventListener('change', (e) => {
                if (e.target.files.length > 0) {
                    selectedFiles = Array.from(e.target.files).map(f => {
                        f.fileId = globalThis.crypto.randomUUID().replaceAll('-', '').substring(0, 9);
                        return f;
                    });
                    showSelectedFile();
                }
            });
        }

        function setupDropZone() {
            const dropZone = document.getElementById('drop-zone');

            dropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropZone.classList.add('drag-over');
            });

            dropZone.addEventListener('dragleave', () => {
                dropZone.classList.remove('drag-over');
            });

            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropZone.classList.remove('drag-over');
                if (e.dataTransfer.files.length > 0) {
                    selectedFiles = Array.from(e.dataTransfer.files).map(f => {
                        f.fileId = globalThis.crypto.randomUUID().replaceAll('-', '').substring(0, 9);
                        return f;
                    });
                    showSelectedFile();
                }
            });

            // Click/tap to open file picker (works on both desktop and mobile)
            dropZone.addEventListener('click', () => {
                document.getElementById('file-input').click();
            });
        }

        function showSelectedFile() {
            if (selectedFiles.length === 0) {
                document.getElementById('send-file-info').classList.add('hidden');
                document.getElementById('file-selection').classList.remove('hidden');
                document.getElementById('create-room-btn').disabled = true;
                updateSendStatus('waiting', 'Select a file to share');
                return;
            }

            const container = document.getElementById('send-files-list');
            const totalSize = selectedFiles.reduce((acc, f) => acc + f.size, 0);
            
            container.innerHTML = selectedFiles.map((file) => `
                <div class="flex flex-col sm:flex-row sm:items-center gap-2 p-3 border-b border-base-200 hover:bg-base-200/40 transition-colors">
                    <div class="text-primary flex-shrink-0 w-8 h-8 flex items-center justify-center">${getFileIcon(file.type)}</div>
                    <div class="flex-1 min-w-0">
                        <div class="font-bold text-sm truncate" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</div>
                        <div class="text-xs text-base-content/60 mt-0.5">${formatBytes(file.size)}</div>
                    </div>
                    <div class="flex-shrink-0">
                        <button class="btn btn-ghost btn-xs text-error sender-remove-btn" data-action="remove-send" data-send-id="${file.fileId}" title="Remove">
                            <svg class="w-3 h-3 file-icon-svg" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                            Remove
                        </button>
                    </div>
                </div>
            `).join('');

            document.getElementById('send-total-size').textContent = formatBytes(totalSize);
            document.getElementById('send-file-info').classList.remove('hidden');
            document.getElementById('file-selection').classList.add('hidden'); // keep drop zone hidden once selected
            document.getElementById('create-room-btn').disabled = false;
            updateSendStatus('waiting', 'File(s) queued. Click "Create Share Link" to continue.');
            
            log(`Selected ${selectedFiles.length} file(s) (${formatBytes(totalSize)})`, 'info');
        }

        function removeSelectedFile(id) {
            const index = selectedFiles.findIndex(f => f.fileId === id);
            if (index !== -1) {
                selectedFiles.splice(index, 1);
                showSelectedFile();
                log('File removed from local queue', 'info');
                sendFileList();
            }
        }

        function showReceiveFileInfo(fileInfo) {
            document.getElementById('receive-file-name').textContent = fileInfo.name;
            document.getElementById('receive-file-size').textContent = formatBytes(fileInfo.size);
            document.getElementById('receive-from-user').textContent = fileInfo.from || 'Unknown';
            document.getElementById('receive-file-info').classList.remove('hidden');
        }

        function createRoom() {
            if (selectedFiles.length === 0) {
                log('No files selected', 'error');
                return;
            }

            if (ws?.readyState !== WebSocket.OPEN) {
                log('Not connected to server. Reconnecting...', 'error');
                initWebSocket();
                setTimeout(createRoom, 1000);
                return;
            }

            const totalSize = selectedFiles.reduce((acc, f) => acc + f.size, 0);
            const fileInfo = {
                name: selectedFiles.length === 1 ? selectedFiles[0].name : `${selectedFiles.length} files`,
                size: totalSize,
                type: selectedFiles.length === 1 ? selectedFiles[0].type : 'multipart'
            };

            const allowAnonymous = document.getElementById('allow-anonymous').checked;

            log(`Creating share room${allowAnonymous ? ' (anonymous access enabled)' : ''}...`, 'info');
            ws.send(JSON.stringify({
                type: 'create_room',
                file_info: fileInfo,
                allow_anonymous: allowAnonymous
            }));

            document.getElementById('create-room-btn').disabled = true;
        }

        function joinRoom() {
            const roomCode = document.getElementById('room-code-input').value.trim();
            if (!roomCode) {
                log('Please enter a share code', 'error');
                return;
            }

            ws.send(JSON.stringify({
                type: 'join_room',
                room_id: roomCode
            }));

            document.getElementById('join-room-btn').disabled = true;
        }

        async function createPeerConnection(isInitiator) {
            peerConnection = new RTCPeerConnection({ iceServers: currentIceServers });

            peerConnection.onicecandidate = (event) => {
                if (event.candidate) {
                    ws.send(JSON.stringify({
                        type: 'ice_candidate',
                        candidate: event.candidate
                    }));
                }
            };

            peerConnection.onconnectionstatechange = () => {
                log(`Connection state: ${peerConnection.connectionState}`, 'info');
                if (peerConnection.connectionState === 'connected') {
                    if (currentMode === 'send') {
                        updateSendStatus('connected', 'P2P connection established!');
                    } else {
                        updateReceiveStatus('connected', 'P2P connection established!');
                    }
                }
            };

            if (isInitiator) {
                dataChannel = peerConnection.createDataChannel('fileTransfer', {
                    ordered: true
                });
                setupDataChannel();

                try {
                    const offer = await peerConnection.createOffer();
                    await peerConnection.setLocalDescription(offer);
                    ws.send(JSON.stringify({
                        type: 'offer',
                        sdp: peerConnection.localDescription
                    }));
                } catch (err) {
                    log(`Error creating offer: ${err}`, 'error');
                }
            } else {
                peerConnection.ondatachannel = (event) => {
                    dataChannel = event.channel;
                    setupDataChannel();
                };
            }
        }

        function setupDataChannel() {
            dataChannel.binaryType = 'arraybuffer';

            dataChannel.onopen = () => {
                log('Data channel opened', 'success');
                if (currentMode === 'send') {
                    startFileSend();
                }
            };

            dataChannel.onclose = () => {
                log('Data channel closed', 'info');
            };

            dataChannel.onerror = (error) => {
                log(`Data channel error: ${error}`, 'error');
            };

            dataChannel.onmessage = (event) => {
                handleDataChannelMessage(event.data);
            };
        }

        async function handleOffer(sdp) {
            createPeerConnection(false);

            await peerConnection.setRemoteDescription(new RTCSessionDescription(sdp));
            const answer = await peerConnection.createAnswer();
            await peerConnection.setLocalDescription(answer);

            ws.send(JSON.stringify({
                type: 'answer',
                sdp: peerConnection.localDescription
            }));
        }

        async function handleAnswer(sdp) {
            await peerConnection.setRemoteDescription(new RTCSessionDescription(sdp));
        }

        function handleIceCandidate(candidate) {
            if (!candidate) return;
            peerConnection?.addIceCandidate(new RTCIceCandidate(candidate))
                .catch(err => log(`Error adding ICE candidate: ${err}`, 'error'));
        }

        function sendFileList() {
            if (dataChannel?.readyState !== 'open') return;
            const fileList = selectedFiles.map((f) => ({
                id: f.fileId,
                name: f.name,
                size: f.size,
                type: f.type
            }));
            dataChannel.send(JSON.stringify({ type: 'file_list', files: fileList }));
        }

        async function startFileSend() {
            if (selectedFiles.length === 0 || !dataChannel) return;
            
            sendFileList();
            updateSendStatus('connected', 'Waiting for recipient to request files...');
        }

        async function processNextRequest() {
            if (pendingRequests.length === 0) {
                isTransferring = false;
                updateSendStatus('connected', 'All requested files sent. Waiting for more requests...');
                return;
            }

            isTransferring = true;
            const requestedId = pendingRequests.shift();
            
            currentFileIndex = selectedFiles.findIndex(f => f.fileId === requestedId);
            if (currentFileIndex === -1) {
                log('Recipient requested a file that was removed', 'warning');
                isTransferring = false;
                processNextRequest();
                return;
            }

            const currentFile = selectedFiles[currentFileIndex];
            updateSendStatus('transferring', `Sending file ${currentFile.name}...`);
            document.getElementById('send-progress').classList.remove('hidden');

            transferStartTime = Date.now();
            let offset = 0;

            const sendNextChunk = async () => {
                const slice = currentFile.slice(offset, offset + CHUNK_SIZE);
                const chunk = await slice.arrayBuffer();
                if (dataChannel.bufferedAmount > CHUNK_SIZE * 10) {
                    setTimeout(() => {
                        void sendNextChunk();
                    }, 50);
                    return;
                }

                dataChannel.send(chunk);
                offset += chunk.byteLength;

                const progress = (offset / currentFile.size) * 100;
                updateSendProgress(progress, offset, currentFile.size);

                if (offset < currentFile.size) {
                    void sendNextChunk();
                } else {
                    dataChannel.send(JSON.stringify({ type: 'end' }));
                    log(`File transfer complete: ${currentFile.name}`, 'success');
                    document.getElementById('send-progress').classList.add('hidden');
                    setTimeout(processNextRequest, 500);
                }
            };
            dataChannel.send(JSON.stringify({
                type: 'start',
                id: requestedId,
                name: currentFile.name,
                size: currentFile.size,
                mime: currentFile.type
            }));

            void sendNextChunk();
        }

        function handleSenderDataMessage(msg) {
            if (msg.type !== 'request_file') return;
            pendingRequests.push(msg.id);
            log(`File requested by recipient: ${msg.id}`, 'info');
            if (!isTransferring) {
                processNextRequest();
            }
        }

        function renderReceivedFilesOverview() {
            const totalSize = availableFiles.reduce((acc, f) => acc + f.size, 0);
            const overviewName = availableFiles.length === 1
                ? availableFiles[0].name
                : `${availableFiles.length} file(s) available`;
            document.getElementById('receive-file-name').textContent = overviewName;
            document.getElementById('receive-file-size').textContent = formatBytes(totalSize);
            document.getElementById('receive-from-user').textContent = currentRoomId || 'Sender';
            document.getElementById('receive-file-info').classList.remove('hidden');
        }

        function handleReceiverFileListMessage(msg) {
            availableFiles = msg.files;
            renderAvailableFiles();
            updateReceiveStatus('connected', 'Select file(s) to download.');
            renderReceivedFilesOverview();
        }

        function markFileAsReceiving(fileId) {
            if (!fileId) return;
            const dBtn = globalThis.AirdCore.queryByDataFileId('.available-file-btn', String(fileId));
            if (dBtn) dBtn.textContent = 'RECEIVING...';
        }

        function handleReceiverStartMessage(msg) {
            expectedFileInfo = {
                id: msg.id,
                name: msg.name,
                size: msg.size,
                mime: msg.mime
            };
            receivedChunks = [];
            receivedSize = 0;
            transferStartTime = Date.now();
            log(`Receiving: ${msg.name} (${formatBytes(msg.size)})`, 'info');
            updateReceiveStatus('transferring', `Receiving ${msg.name}...`);
            document.getElementById('receive-progress').classList.remove('hidden');
            markFileAsReceiving(expectedFileInfo.id);
        }

        function handleReceiverDataMessage(msg) {
            if (msg.type === 'file_list') {
                handleReceiverFileListMessage(msg);
                return;
            }
            if (msg.type === 'start') {
                handleReceiverStartMessage(msg);
                return;
            }
            if (msg.type === 'end') {
                completeReceive();
            }
        }

        function handleBinaryDataChunk(data) {
            if (currentMode !== 'receive') return;
            receivedChunks.push(data);
            receivedSize += data.byteLength;
            if (!expectedFileInfo) return;
            const progress = (receivedSize / expectedFileInfo.size) * 100;
            updateReceiveProgress(progress, receivedSize, expectedFileInfo.size);
        }

        function handleDataChannelMessage(data) {
            const TransferServiceCtor = globalThis.P2PPatterns?.TransferService;
            if (TransferServiceCtor) {
                if (!handleDataChannelMessage._service) {
                    handleDataChannelMessage._service = new TransferServiceCtor({
                        onStringMessage: (msg) => {
                            if (currentMode === 'send') {
                                handleSenderDataMessage(msg);
                                return;
                            }
                            handleReceiverDataMessage(msg);
                        },
                        onBinaryMessage: (chunk) => {
                            handleBinaryDataChunk(chunk);
                        }
                    });
                }
                handleDataChannelMessage._service.handleIncoming(data);
                return;
            }
            if (typeof data !== 'string') {
                handleBinaryDataChunk(data);
                return;
            }
            const msg = JSON.parse(data);
            if (currentMode === 'send') {
                handleSenderDataMessage(msg);
                return;
            }
            handleReceiverDataMessage(msg);
        }

        function completeReceive() {
            const blob = new Blob(receivedChunks, { type: expectedFileInfo.mime || 'application/octet-stream' });

            const fileEntry = {
                id: Date.now() + '_' + globalThis.crypto.randomUUID().replaceAll('-', '').substring(0, 9),
                originalId: expectedFileInfo.id,
                name: expectedFileInfo.name,
                size: expectedFileInfo.size,
                mime: expectedFileInfo.mime || 'application/octet-stream',
                blob: blob,
                url: URL.createObjectURL(blob),
                receivedAt: new Date()
            };

            receivedFiles.push(fileEntry);

            document.getElementById('receive-progress').classList.add('hidden');
            
            if (expectedFileInfo?.id) {
                const btn = globalThis.AirdCore.queryByDataFileId('.available-file-btn', String(expectedFileInfo.id));
                if (btn) {
                    btn.textContent = 'DOWNLOADED ✓';
                    btn.disabled = true;
                }
            }

            updateReceiveStatus('connected', `File received! ${receivedFiles.length} file(s) downloaded.`);
            log(`File received: ${expectedFileInfo.name} - Ready to download`, 'success');

            renderReceivedFiles();

            receivedChunks = [];
            receivedSize = 0;
            expectedFileInfo = null;
        }

        globalThis.requestFile = function(id) {
            if (dataChannel?.readyState !== 'open') {
                log('No active connection to sender', 'error');
                return;
            }
            
            const btn = globalThis.AirdCore.queryByDataFileId('.available-file-btn', String(id));
            if (btn?.disabled) return; // Prevent duplicate requests
            
            requestedFiles.add(id);
            dataChannel.send(JSON.stringify({ type: 'request_file', id: id }));
            
            if (btn) {
                btn.textContent = 'REQUESTED...';
                btn.disabled = true;
            }
            const cb = globalThis.AirdCore.queryByDataFileId('.available-file-checkbox', String(id));
            if (cb) {
                cb.disabled = true;
                cb.checked = false; // Disconnect it from select-all logic if requested
            }
        };

        function downloadSelectedAvailableFiles() {
            const checkboxes = document.querySelectorAll('.available-file-checkbox:checked');
            const selectedIds = Array.from(checkboxes).map(cb => cb.dataset.fileId);
            
            if (selectedIds.length === 0) {
                log('No files selected', 'error');
                return;
            }

            selectedIds.forEach((id, i) => {
                setTimeout(() => {
                    requestFile(id);
                }, i * 200); // Stagger requests slightly to not choke the WS/RTC buffers
            });
        }

        function renderAvailableFiles() {
            const container = document.getElementById('available-files-list');
            const section = document.getElementById('available-files-section');

            if (availableFiles.length === 0) {
                section.classList.add('hidden');
                container.innerHTML = '';
                return;
            }

            section.classList.remove('hidden');

            container.innerHTML = availableFiles.map((file) => {
                const isRequested = requestedFiles.has(file.id);
                const isDownloaded = receivedFiles.some(f => f.originalId === file.id);
                const isBusy = isRequested || isDownloaded;
                
                let btnText = 'DOWNLOAD';
                if(isDownloaded) btnText = 'DOWNLOADED ✓';
                else if(isRequested) btnText = 'REQUESTED...';

                return `
                <div class="flex flex-col sm:flex-row sm:items-center gap-2 p-3 border-b border-base-200 hover:bg-base-200/40 transition-colors">
                    <input type="checkbox" class="available-file-checkbox checkbox checkbox-primary checkbox-sm flex-shrink-0" ${isBusy ? 'disabled' : 'checked'} data-file-id="${escapeHtml(file.id)}">
                    <div class="text-primary flex-shrink-0 w-8 h-8 flex items-center justify-center">${getFileIcon(file.type)}</div>
                    <div class="flex-1 min-w-0">
                        <div class="font-bold text-sm truncate" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</div>
                        <div class="text-xs text-base-content/60 mt-0.5">${formatBytes(file.size)}</div>
                    </div>
                    <div class="flex-shrink-0">
                        <button class="btn btn-sm available-file-btn ${isBusy ? 'btn-disabled' : 'btn-primary'} w-full sm:w-auto" data-file-id="${escapeHtml(file.id)}" ${isBusy ? 'disabled' : ''}>
                            ${btnText}
                        </button>
                    </div>
                </div>
            `;
            }).join('');
        }

        function renderReceivedFiles() {
            const container = document.getElementById('received-files-list');
            const section = document.getElementById('received-files-section');

            if (receivedFiles.length === 0) {
                section.classList.add('hidden');
                container.innerHTML = '';
                return;
            }

            section.classList.remove('hidden');

            container.innerHTML = receivedFiles.map(file => `
                <div class="flex flex-col sm:flex-row sm:items-center gap-2 p-3 border-b border-base-200 hover:bg-base-200/40 transition-colors" data-file-id="${escapeHtml(file.id)}">
                    <input type="checkbox" class="received-file-checkbox checkbox checkbox-primary checkbox-sm flex-shrink-0" checked data-file-id="${escapeHtml(file.id)}">
                    <div class="text-primary flex-shrink-0 w-8 h-8 flex items-center justify-center">${getFileIcon(file.mime)}</div>
                    <div class="flex-1 min-w-0">
                        <div class="font-bold text-sm truncate" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</div>
                        <div class="text-xs text-base-content/60 mt-0.5">
                            ${formatBytes(file.size)} &bull; ${file.receivedAt.toLocaleTimeString()}
                        </div>
                    </div>
                    <div class="flex gap-2 flex-shrink-0">
                        <button class="btn btn-primary btn-xs received-file-btn" data-action="download" data-file-id="${escapeHtml(file.id)}" title="Download">
                            <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            Download
                        </button>
                        <button class="btn btn-ghost btn-xs text-error received-file-btn" data-action="remove" data-file-id="${escapeHtml(file.id)}" title="Remove">
                            <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                    </div>
                </div>
            `).join('');
        }

        // SVG icons for different file types
        const FILE_ICONS = {
            image: '<svg class="sq-style-bccdd9" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
            video: '<svg class="sq-style-e33b5b" viewBox="0 0 24 24"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>',
            audio: '<svg class="sq-style-f79f7b" viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
            pdf: '<svg class="sq-style-610bd2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>',
            archive: '<svg class="sq-style-51f0e3" viewBox="0 0 24 24"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg>',
            document: '<svg class="sq-style-369042" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
            spreadsheet: '<svg class="sq-style-f89798" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="12" y1="9" x2="12" y2="21"/></svg>',
            code: '<svg class="sq-style-e60fe6" viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
            default: '<svg class="sq-style-166c56" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
        };

        function getFileIcon(mime) {
            if (!mime) return FILE_ICONS.default;
            if (mime.startsWith('image/')) return FILE_ICONS.image;
            if (mime.startsWith('video/')) return FILE_ICONS.video;
            if (mime.startsWith('audio/')) return FILE_ICONS.audio;
            if (mime.includes('pdf')) return FILE_ICONS.pdf;
            if (mime.includes('zip') || mime.includes('archive') || mime.includes('compressed') || mime.includes('tar') || mime.includes('rar')) return FILE_ICONS.archive;
            if (mime.includes('spreadsheet') || mime.includes('excel') || mime.includes('csv')) return FILE_ICONS.spreadsheet;
            if (mime.includes('javascript') || mime.includes('json') || mime.includes('xml') || mime.includes('html') || mime.includes('css')) return FILE_ICONS.code;
            if (mime.includes('text') || mime.includes('document') || mime.includes('word')) return FILE_ICONS.document;
            return FILE_ICONS.default;
        }

        function escapeHtml(text) {
            return globalThis.AirdCore.escapeHtml(text);
        }

        function downloadFile(fileId) {
            const file = receivedFiles.find(f => f.id === fileId);
            if (!file) {
                log('File not found', 'error');
                return;
            }

            const a = document.createElement('a');
            a.href = file.url;
            a.download = file.name;
            document.body.appendChild(a);
            a.click();
            a.remove();

            log(`Downloaded: ${file.name}`, 'success');
        }

        function downloadAllFiles() {
            // Get only checked files
            const checkboxes = document.querySelectorAll('.received-file-checkbox:checked');
            const selectedIds = Array.from(checkboxes).map(cb => cb.dataset.fileId);

            if (selectedIds.length === 0) {
                log('No files selected for download', 'error');
                return;
            }

            // Download each selected file with a small delay to prevent browser blocking
            selectedIds.forEach((fileId, index) => {
                setTimeout(() => {
                    downloadFile(fileId);
                }, index * 300);
            });

            log(`Downloading ${selectedIds.length} file(s)...`, 'info');
        }

        function removeFile(fileId) {
            const fileIndex = receivedFiles.findIndex(f => f.id === fileId);
            if (fileIndex !== -1) {
                const file = receivedFiles[fileIndex];
                URL.revokeObjectURL(file.url); // Clean up blob URL
                receivedFiles.splice(fileIndex, 1);
                renderReceivedFiles();
                log(`Removed: ${file.name}`, 'info');
            }
        }

        function clearReceivedFiles() {
            // Clean up all blob URLs
            receivedFiles.forEach(file => {
                URL.revokeObjectURL(file.url);
            });
            receivedFiles = [];
            renderReceivedFiles();
            log('Cleared all received files', 'info');
        }

        function updateSendProgress(percent, transferred, total) {
            const fill = document.getElementById('send-progress-fill');
            fill.style.width = `${percent}%`;
            fill.textContent = `${Math.round(percent)}%`;

            document.getElementById('send-progress-transferred').textContent =
                `${formatBytes(transferred)} / ${formatBytes(total)}`;

            const elapsed = (Date.now() - transferStartTime) / 1000;
            const speed = transferred / elapsed;
            document.getElementById('send-progress-speed').textContent = `${formatBytes(speed)}/s`;
        }

        function updateReceiveProgress(percent, transferred, total) {
            const fill = document.getElementById('receive-progress-fill');
            fill.style.width = `${percent}%`;
            fill.textContent = `${Math.round(percent)}%`;

            document.getElementById('receive-progress-transferred').textContent =
                `${formatBytes(transferred)} / ${formatBytes(total)}`;

            const elapsed = (Date.now() - transferStartTime) / 1000;
            const speed = transferred / elapsed;
            document.getElementById('receive-progress-speed').textContent = `${formatBytes(speed)}/s`;
        }

        const STATUS_CLASSES = {
            waiting:     'alert alert-neutral',
            connected:   'alert alert-info',
            transferring:'alert alert-primary',
            success:     'alert alert-success',
            error:       'alert alert-error',
        };
        const STATUS_BASE = 'status-indicator shadow-sm mb-6 flex items-center gap-3 p-3 font-bold text-sm tracking-wide uppercase border';

        function updateSendStatus(status, text) {
            const el = document.getElementById('send-status');
            el.className = `${STATUS_BASE} ${STATUS_CLASSES[status] || STATUS_CLASSES.waiting} border-base-300`;
            document.getElementById('send-status-text').textContent = text;
        }

        function updateReceiveStatus(status, text) {
            const el = document.getElementById('receive-status');
            el.className = `${STATUS_BASE} ${STATUS_CLASSES[status] || STATUS_CLASSES.waiting} border-base-300`;
            document.getElementById('receive-status-text').textContent = text;
        }

        function copyShareCode() {
            const code = currentRoomId || document.getElementById('share-code').textContent.trim();
            if (!code || code === '------') {
                log('No share code available yet', 'error');
                return;
            }
            copyToClipboard(code, 'Share code copied to clipboard');
        }

        function copyShareLink() {
            const code = currentRoomId || document.getElementById('share-code').textContent.trim();
            if (!code || code === '------') {
                log('No share code available yet', 'error');
                return;
            }
            const link = buildP2pShareJoinUrl(code);
            copyToClipboard(link, 'Share link copied to clipboard');
        }

        function generateShareQRCode(roomId) {
            const link = buildP2pShareJoinUrl(roomId);
            const canvas = document.getElementById('qr-code-canvas');
            const container = document.getElementById('qr-code-container');
            if (!link || !canvas || !container) {
                if (container) container.style.display = 'none';
                return;
            }
            renderP2pShareQrOnCanvas(canvas, link).then((success) => {
                if (!container.isConnected) return;
                if (success) {
                    container.style.display = 'flex';
                    log('QR code generated for share link', 'info');
                } else {
                    container.style.display = 'none';
                    log(
                        'Failed to generate QR (load error?) — use Copy link or room code.',
                        'error'
                    );
                }
            }).catch(() => {
                if (container.isConnected) {
                    container.style.display = 'none';
                    log('Failed to generate QR — use Copy link or room code.', 'error');
                }
            });
        }

        let qrCodeVisible = true;
        function toggleQRCode() {
            const container = document.getElementById('qr-code-container');
            const btn = document.getElementById('qr-toggle-btn');

            if (qrCodeVisible) {
                container.style.display = 'none';
                btn.textContent = 'Show QR';
                qrCodeVisible = false;
            } else {
                container.style.display = 'flex';
                btn.textContent = 'Hide QR';
                qrCodeVisible = true;
            }
        }

        async function copyToClipboard(text, successMessage) {
            if (!navigator.clipboard?.writeText) {
                log('Clipboard unavailable. Copy manually: ' + text, 'error');
                globalThis.prompt('Copy this value:', text);
                return;
            }
            try {
                await navigator.clipboard.writeText(text);
                log(successMessage, 'success');
            } catch (err) {
                console.error('Clipboard API failed:', err);
                log('Failed to copy. Please copy manually: ' + text, 'error');
                globalThis.prompt('Copy this value:', text);
            }
        }

        function cancelCurrentTransfer() {
            closePeerConnection();
            if (currentRoomId) {
                ws.send(JSON.stringify({ type: 'leave_room' }));
            }
            globalThis.location.reload();
        }

        function cancelSend() {
            cancelCurrentTransfer();
        }

        function cancelReceive() {
            cancelCurrentTransfer();
        }

        function closePeerConnection() {
            if (dataChannel) {
                dataChannel.close();
                dataChannel = null;
            }
            if (peerConnection) {
                peerConnection.close();
                peerConnection = null;
            }
        }

        function formatBytes(bytes) {
            return globalThis.AirdCore.formatBytes(bytes);
        }

        function log(message, type = '') {
            const container = document.getElementById('log-container');
            const entry = document.createElement('div');
            entry.className = `log-entry ${type}`;
            entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
            container.appendChild(entry);
            container.scrollTop = container.scrollHeight;
        }

        // Event listeners for CSP compliance
        document.addEventListener('DOMContentLoaded', function () {
            document.getElementById('send-mode-btn')?.addEventListener('click', function () {
                selectMode('send');
            });
            document.getElementById('receive-mode-btn')?.addEventListener('click', function () {
                selectMode('receive');
            });
            document.getElementById('drop-zone')?.addEventListener('click', function () {
                document.getElementById('file-input').click();
            });
            document.getElementById('copy-code-btn')?.addEventListener('click', copyShareCode);
            document.getElementById('copy-link-btn')?.addEventListener('click', copyShareLink);
            document.getElementById('qr-toggle-btn')?.addEventListener('click', toggleQRCode);
            document.getElementById('create-room-btn')?.addEventListener('click', createRoom);
            document.getElementById('cancel-send-btn')?.addEventListener('click', cancelSend);
            document.getElementById('download-all-btn')?.addEventListener('click', downloadAllFiles);
            document.getElementById('clear-files-btn')?.addEventListener('click', clearReceivedFiles);
            document.getElementById('join-room-btn')?.addEventListener('click', joinRoom);
            document.getElementById('cancel-receive-btn')?.addEventListener('click', cancelReceive);

            document.getElementById('download-selected-available-btn')?.addEventListener('click', downloadSelectedAvailableFiles);
            
            document.getElementById('select-all-available')?.addEventListener('change', function(e) {
                const checked = e.target.checked;
                document.querySelectorAll('.available-file-checkbox').forEach(cb => {
                    if(!cb.disabled) cb.checked = checked;
                });
            });

            document.getElementById('select-all-received')?.addEventListener('change', function(e) {
                const checked = e.target.checked;
                document.querySelectorAll('.received-file-checkbox').forEach(cb => {
                    cb.checked = checked;
                });
            });

            // Event delegation for dynamically added available files (CSP compliant)
            document.getElementById('available-files-list')?.addEventListener('click', function(e) {
                const btn = e.target.closest('.available-file-btn');
                if (!btn) return;
                const id = btn.dataset.fileId;
                if (!btn.disabled) {
                    requestFile(id);
                }
            });

            // Event delegation for Sender managing local files list
            document.getElementById('send-files-list')?.addEventListener('click', function(e) {
                const btn = e.target.closest('.sender-remove-btn');
                if (!btn) return;
                const id = btn.dataset.sendId;
                removeSelectedFile(id);
            });

            // Event delegation for dynamically added received files
            document.getElementById('received-files-list')?.addEventListener('click', function (e) {
                const btn = e.target.closest('.received-file-btn');
                if (!btn) return;
                const fileId = btn.dataset.fileId;
                if (btn.dataset.action === 'download') {
                    downloadFile(fileId);
                } else if (btn.dataset.action === 'remove') {
                    removeFile(fileId);
                }
            });
        });

