HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>Hotel Voice Agent</title>
    <style>
        body { font-family: Arial, sans-serif; padding: 40px; background: #f5f5f5; }
        .container { max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; }
        button {
            padding: 15px 30px;
            font-size: 18px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            margin: 5px;
        }
        button:disabled { background: #ccc; cursor: not-allowed; }
        button#stopBtn { background: #dc3545; }
        .status { margin-top: 20px; padding: 15px; background: #e9ecef; border-radius: 8px; }
        .transcript { margin-top: 20px; padding: 15px; background: #fff3cd; border-radius: 8px; min-height: 40px; }
        .response { margin-top: 20px; padding: 15px; background: #d4edda; border-radius: 8px; min-height: 40px; }
        .ticket { margin-top: 10px; padding: 10px; background: #cce5ff; border-radius: 8px; display: none; white-space: pre-wrap; font-family: monospace; }
        .debug { margin-top: 20px; padding: 10px; background: #f8f9fa; border-radius: 8px; font-size: 12px; color: #666; word-break: break-all; max-height: 100px; overflow-y: auto; }
        .error-box { margin-top: 10px; padding: 15px; background: #f8d7da; border-radius: 8px; display: none; color: #721c24; }
        a { display: inline-block; margin-top: 20px; color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🏨 Hotel Voice Agent</h1>
        <p>Click the button and speak naturally.</p>
        <ul>
            <li>"I need towels in room 402"</li>
            <li>"What's the Wi-Fi password?"</li>
            <li>"Towels and my AC is broken"</li>
        </ul>
        <button id="startBtn">🎤 Start Speaking</button>
        <button id="stopBtn" disabled>⏹ Stop</button>
        <div class="status" id="status">🔌 Connecting...</div>
        <div class="transcript" id="transcript">📝 Transcript: </div>
        <div class="response" id="response">🤖 Response: </div>
        <div class="ticket" id="ticket"></div>
        <div class="debug" id="debug">🔍 Ready</div>
        <div class="error-box" id="errorBox"></div>
        <a href="/tickets">📋 View All Tickets</a>
    </div>
    <script>
        var ws = null;
        var audioContext = null;
        var mediaStream = null;
        var source = null;
        var processor = null;
        var isRecording = false;
        var reconnectAttempts = 0;
        var maxReconnectAttempts = 5;

        var startBtn = document.getElementById("startBtn");
        var stopBtn = document.getElementById("stopBtn");
        var statusDiv = document.getElementById("status");
        var transcriptDiv = document.getElementById("transcript");
        var responseDiv = document.getElementById("response");
        var ticketDiv = document.getElementById("ticket");
        var debugDiv = document.getElementById("debug");
        var errorBox = document.getElementById("errorBox");

        function updateStatus(msg) {
            statusDiv.textContent = msg;
            console.log("Status:", msg);
        }
        function setTranscript(text) {
            transcriptDiv.textContent = "📝 Transcript: " + text;
        }
        function setResponse(text) {
            responseDiv.textContent = "🤖 Response: " + text;
        }
        function showTicket(details) {
            ticketDiv.style.display = "block";
            ticketDiv.textContent = "🎫 Ticket: " + JSON.stringify(details, null, 2);
        }
        function setDebug(msg) {
            debugDiv.textContent = "🔍 " + msg;
            console.log("Debug:", msg);
        }
        function showError(msg) {
            errorBox.style.display = "block";
            errorBox.textContent = "❌ " + msg;
            updateStatus("❌ Error");
        }
        function hideError() {
            errorBox.style.display = "none";
        }

        function connectWebSocket() {
            var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            var wsUrl = protocol + "//" + window.location.host + "/ws";
            setDebug("Connecting to: " + wsUrl);

            ws = new WebSocket(wsUrl);

            ws.onopen = function () {
                setDebug("✅ WebSocket connected");
                updateStatus('✅ Connected! Click "Start Speaking"');
                startBtn.disabled = false;
                hideError();
                reconnectAttempts = 0;
            };

            ws.onmessage = function (event) {
                try {
                    var data = JSON.parse(event.data);
                    console.log("Received:", data);

                    if (data.type === "transcript") {
                        setTranscript(data.text);
                        setDebug('STT: "' + data.text + '"');
                    } else if (data.type === "response") {
                        setResponse(data.text);
                    } else if (data.type === "ticket_created") {
                        showTicket(data.details);
                        setResponse("✅ Ticket created!");
                    } else if (data.type === "error") {
                        showError(data.text);
                        setResponse("❌ Error: " + data.text);
                    }
                } catch (e) {
                    console.error("Parse error:", e);
                    setDebug("Parse error: " + e.message);
                }
            };

            ws.onclose = function () {
                setDebug("❌ WebSocket closed");
                updateStatus("❌ Disconnected. Reconnecting...");
                startBtn.disabled = true;
                stopBtn.disabled = true;

                if (reconnectAttempts < maxReconnectAttempts) {
                    reconnectAttempts++;
                    setDebug("Reconnecting attempt " + reconnectAttempts + "/" + maxReconnectAttempts);
                    setTimeout(connectWebSocket, 2000);
                } else {
                    showError("Failed to connect to server. Please refresh the page.");
                    updateStatus("❌ Connection failed");
                }
            };

            ws.onerror = function (error) {
                console.error("WebSocket error:", error);
                setDebug("❌ WebSocket error");
            };
        }

        function startRecording() {
            hideError();
            ticketDiv.style.display = "none";
            setDebug("Requesting microphone...");

            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                var url = window.location.href;
                if (url.indexOf("localhost") === -1 && url.indexOf("127.0.0.1") === -1) {
                    showError("Microphone access requires localhost. Please use http://localhost:8080");
                } else {
                    showError("Your browser does not support microphone access.");
                }
                updateStatus("❌ Microphone error");
                return;
            }

            navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, sampleRate: 16000 } })
                .then(function (stream) {
                    mediaStream = stream;
                    setDebug("✅ Microphone access granted");

                    audioContext = new AudioContext({ sampleRate: 16000 });
                    source = audioContext.createMediaStreamSource(mediaStream);

                    processor = audioContext.createScriptProcessor(4096, 1, 1);
                    processor.onaudioprocess = function (e) {
                        if (ws && ws.readyState === WebSocket.OPEN && isRecording) {
                            var inputData = e.inputBuffer.getChannelData(0);
                            var pcmData = new Int16Array(inputData.length);
                            for (var i = 0; i < inputData.length; i++) {
                                pcmData[i] = Math.round(Math.max(-1, Math.min(1, inputData[i])) * 32767);
                            }
                            var bytes = new Uint8Array(pcmData.buffer);
                            var binary = "";
                            for (var j = 0; j < bytes.length; j++) {
                                binary += String.fromCharCode(bytes[j]);
                            }
                            ws.send(JSON.stringify({ type: "audio", data: window.btoa(binary) }));
                        }
                    };

                    source.connect(processor);
                    processor.connect(audioContext.destination);

                    isRecording = true;
                    updateStatus("🔴 Recording... Speak now!");
                    startBtn.disabled = true;
                    stopBtn.disabled = false;
                    setDebug("🔴 Recording started");
                })
                .catch(function (err) {
                    console.error("Recording error:", err);
                    showError(err.message);
                    updateStatus("❌ Microphone error");
                    startBtn.disabled = false;
                    stopBtn.disabled = true;
                    setDebug("❌ " + err.message);
                });
        }

        function stopRecording() {
            isRecording = false;

            if (processor) {
                try { processor.disconnect(); } catch (e) {}
                processor = null;
            }
            if (source) {
                try { source.disconnect(); } catch (e) {}
                source = null;
            }
            if (mediaStream) {
                mediaStream.getTracks().forEach(function (track) { track.stop(); });
                mediaStream = null;
            }
            if (audioContext && audioContext.state !== "closed") {
                try { audioContext.close(); } catch (e) {}
                audioContext = null;
            }

            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "end" }));
            }

            updateStatus("⏹ Processing...");
            startBtn.disabled = false;
            stopBtn.disabled = true;
            setDebug("⏹ Processing audio...");
        }

        startBtn.addEventListener("click", startRecording);
        stopBtn.addEventListener("click", stopRecording);

        connectWebSocket();
        setDebug("✅ Ready! Click Start Speaking");
    </script>
</body>
</html>
"""
