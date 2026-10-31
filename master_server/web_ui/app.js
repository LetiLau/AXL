/**
 * A-X-L CYBERNETIC VISOR // HUD CLIENT ENGINE
 * Handles:
 * 1. WebSocket link to Master Server (/ws/ui)
 * 2. 60FPS Canvas Visor Animation (Idle, Listening, Thinking, Speaking, Alert)
 * 3. Biometric telemetry & Live Subtitles
 * 4. Real-Time & Fallback Hardware Telemetry HUD Panel
 * 5. One-Way Security Mode Lock (Friendly -> Restrict / Paranoid)
 * 6. OLED Pixel-Shifting anti-burn-in protection (Samsung Galaxy A33)
 */

(function () {
    "use strict";

    // --- DOM Elements ---
    const body = document.body;
    const visorVideo = document.getElementById("visor-video");
    const canvas = document.getElementById("visor-canvas");
    const ctx = canvas ? canvas.getContext("2d") : null;
    const clockTime = document.getElementById("clock-time");
    const clockDate = document.getElementById("clock-date");
    const secModeBadge = document.getElementById("sec-mode-badge");
    const modeSelectorPopup = document.getElementById("mode-selector-popup");
    const connBadge = document.getElementById("connection-badge");
    const serverTelemBtn = document.getElementById("server-telem-btn");
    const slaveTelemBtn = document.getElementById("slave-telem-btn");
    const telemetryBtn = slaveTelemBtn; // alias for backwards compatibility
    const telemTitle = document.getElementById("telem-title");
    const stateLabel = document.getElementById("visor-state-label");
    const stateSub = document.getElementById("visor-sub-label");
    const bioIdentity = document.getElementById("bio-identity");
    const bioScoreBar = document.getElementById("bio-score-bar");
    const bioScoreText = document.getElementById("bio-score-text");
    const bioAuthStatus = document.getElementById("bio-auth-status");
    const userTranscript = document.getElementById("user-transcript");
    const axlReply = document.getElementById("axl-reply");
    const fullscreenBtn = document.getElementById("fullscreen-btn");
    const muteBtn = document.getElementById("mute-btn");
    const muteBtnIcon = document.getElementById("mute-btn-icon");
    const muteBtnLabel = document.getElementById("mute-btn-label");
    const askBtn = document.getElementById("ask-btn");
    const askBtnLabel = document.getElementById("ask-btn-label");

    // --- Telemetry Elements ---
    const telemetryPanel = document.getElementById("telemetry-panel");
    const telemCloseBtn = document.getElementById("telem-close-btn");
    const telemNodeBadge = document.getElementById("telem-node-badge");
    const telemCpuUsage = document.getElementById("telem-cpu-usage");
    const telemCpuTemp = document.getElementById("telem-cpu-temp");
    const telemCpuFreq = document.getElementById("telem-cpu-freq");
    const telemCpuCores = document.getElementById("telem-cpu-cores");
    const telemCpuBar = document.getElementById("telem-cpu-bar");
    const telemRamUsed = document.getElementById("telem-ram-used");
    const telemRamTotal = document.getElementById("telem-ram-total");
    const telemRamFree = document.getElementById("telem-ram-free");
    const telemRamPct = document.getElementById("telem-ram-pct");
    const telemRamBar = document.getElementById("telem-ram-bar");
    const telemGpuList = document.getElementById("telem-gpu-list");
    const telemGpuGlobalTemp = document.getElementById("telem-gpu-global-temp");
    const telemFanRpm = document.getElementById("telem-fan-rpm");
    const telemFanStatus = document.getElementById("telem-fan-status");
    const telemFanLabel = document.getElementById("telem-fan-label");

    // --- State Variables ---
    let currentState = "IDLE";
    let securityMode = "friendly";
    let isMuted = false;
    let isTelemetryActive = false;
    let telemetryTarget = "fedora";
    let telemetrySimInterval = null;
    let lastRealTelemetryTime = 0;
    let simCpuPercent = 22;
    let simRamPercent = 46;
    let simGpuPercent = 28;
    let simFanRpm = 1720;
    let ws = null;
    let wsReconnectTimeout = null;
    let animFrameId = null;
    let animTime = 0;
    let idleInactivityTimer = null;
    let targetMicLevel = 0.0;
    let currentMicLevel = 0.0;

    // Ensure telemetry panel is strictly hidden at startup
    if (telemetryPanel) {
        telemetryPanel.hidden = true;
        telemetryPanel.style.display = "none";
    }

    // Canvas resolution setup
    function resizeCanvas() {
        if (!canvas) return;
        const rect = canvas.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    window.addEventListener("resize", resizeCanvas);
    resizeCanvas();

    // ==========================================================================
    // 1. OLED PIXEL-SHIFTING & SUBDUED AMOLED POWER-SAVING (BURN-IN PROTECTION)
    // ==========================================================================
    const PIXEL_SHIFT_INTERVAL_MS = 60000;
    const SUBDUED_INACTIVITY_MS = 45000; // 45 seconds of idle inactivity before dimming

    function triggerPixelShift() {
        const shiftX = (Math.floor(Math.random() * 17) - 8);
        const shiftY = (Math.floor(Math.random() * 17) - 8);
        document.documentElement.style.setProperty("--hud-shift-x", `${shiftX}px`);
        document.documentElement.style.setProperty("--hud-shift-y", `${shiftY}px`);
    }
    setInterval(triggerPixelShift, PIXEL_SHIFT_INTERVAL_MS);

    function wakeScreen() {
        if (body.classList.contains("hud-subdued")) {
            body.classList.remove("hud-subdued");
        }
    }

    function dimScreen() {
        if (currentState === "IDLE") {
            body.classList.add("hud-subdued");
        }
    }

    function resetInactivityTimer() {
        wakeScreen();
        if (idleInactivityTimer) {
            clearTimeout(idleInactivityTimer);
            idleInactivityTimer = null;
        }
        if (currentState === "IDLE") {
            idleInactivityTimer = setTimeout(() => {
                dimScreen();
            }, SUBDUED_INACTIVITY_MS);
        }
    }

    let lastUserActivityTime = 0;
    function onThrottledUserActivity() {
        const now = Date.now();
        // Throttle high-frequency cursor movements to once per 500ms
        if (now - lastUserActivityTime > 500) {
            lastUserActivityTime = now;
            resetInactivityTimer();
        }
    }

    // Capture phase listeners ensure user interaction wakes screen even if UI buttons stop propagation
    window.addEventListener("pointerdown", resetInactivityTimer, { passive: true, capture: true });
    window.addEventListener("keydown", resetInactivityTimer, { passive: true, capture: true });
    window.addEventListener("pointermove", onThrottledUserActivity, { passive: true, capture: true });
    window.addEventListener("touchstart", resetInactivityTimer, { passive: true, capture: true });

    // ==========================================================================
    // 2. TACTICAL CLOCK & DATE
    // ==========================================================================
    function updateClock() {
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, "0");
        const mm = String(now.getMinutes()).padStart(2, "0");
        const ss = String(now.getSeconds()).padStart(2, "0");
        if (clockTime) clockTime.textContent = `${hh}:${mm}:${ss}`;

        const options = { weekday: "short", day: "2-digit", month: "short", year: "numeric" };
        if (clockDate) clockDate.textContent = now.toLocaleDateString("it-IT", options).toUpperCase();
    }
    setInterval(updateClock, 500);
    updateClock();

    // ==========================================================================
    // 3. FULLSCREEN CONTROLLER
    // ==========================================================================
    if (fullscreenBtn) {
        fullscreenBtn.addEventListener("click", () => {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(() => {});
            } else {
                document.exitFullscreen().catch(() => {});
            }
        });
    }

    // ==========================================================================
    // 4. WEBSOCKET HUD CLIENT
    // ==========================================================================
    function connectWebSocket() {
        if (wsReconnectTimeout) clearTimeout(wsReconnectTimeout);

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const host = window.location.host || "127.0.0.1:8000";
        const wsUrl = `${protocol}//${host}/ws/ui`;

        if (connBadge) {
            connBadge.className = "badge badge-offline";
            connBadge.textContent = "WS: CONNECTING";
        }

        try {
            ws = new WebSocket(wsUrl);
        } catch (e) {
            scheduleReconnect();
            return;
        }

        ws.onopen = () => {
            if (connBadge) {
                connBadge.className = "badge badge-online";
                connBadge.textContent = "WS: ONLINE";
            }
            if (isTelemetryActive) {
                ws.send(JSON.stringify({ type: "REQUEST_TELEMETRY" }));
            }
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleServerMessage(msg);
            } catch (err) {
                console.error("[WS] Invalid JSON payload:", event.data);
            }
        };

        ws.onclose = () => {
            if (connBadge) {
                connBadge.className = "badge badge-offline";
                connBadge.textContent = "WS: OFFLINE";
            }
            scheduleReconnect();
        };

        ws.onerror = () => {
            try { ws.close(); } catch (_) {}
        };
    }

    function scheduleReconnect() {
        if (wsReconnectTimeout) clearTimeout(wsReconnectTimeout);
        wsReconnectTimeout = setTimeout(connectWebSocket, 2000);
    }

    if (connBadge) {
        connBadge.addEventListener("click", () => {
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                connectWebSocket();
            }
        });
    }

    // ==========================================================================
    // 5. SERVER MESSAGE DISPATCHER
    // ==========================================================================
    function handleServerMessage(msg) {
        // Notice: High-frequency background messages (MIC_LEVEL, TELEMETRY_UPDATE)
        // MUST NOT reset the inactivity timer, otherwise the screen would never dim!

        switch (msg.type) {
            case "INIT":
                if (msg.security_mode) updateSecurityMode(msg.security_mode);
                if (msg.state) setVisorState(msg.state);
                if (msg.is_muted !== undefined) updateMuteUI(msg.is_muted);
                if (msg.telemetry_active !== undefined) {
                    setTelemetryMode(msg.telemetry_active, msg.telemetry_target || "slave", false);
                }
                resetInactivityTimer();
                break;

            case "STATE_CHANGE":
                // Handled inside setVisorState (wakes if active, or re-arms timer if IDLE)
                setVisorState(msg.state, msg.label, msg.sub);
                break;

            case "TELEMETRY_MODE":
                resetInactivityTimer();
                setTelemetryMode(msg.enabled, msg.target || "slave", false);
                if (msg.data) updateTelemetryData(msg.data, msg.target || "slave");
                break;

            case "TELEMETRY_UPDATE":
                // Passive background telemetry - do NOT reset inactivity timer!
                if (msg.data) {
                    updateTelemetryData(msg.data, msg.target || telemetryTarget);
                } else if (msg.error) {
                    // Node unreachable: keep live fallback metrics running so values update smoothly
                    if (Date.now() - lastRealTelemetryTime > 2500) {
                        updateTelemetryData(generateFallbackTelemetry(), msg.target || telemetryTarget);
                    }
                }
                break;

            case "AUTH_EVENT":
                resetInactivityTimer();
                updateBiometrics(msg);
                break;

            case "TRANSCRIPT":
                resetInactivityTimer();
                if (msg.user_text && userTranscript) userTranscript.textContent = `"${msg.user_text}"`;
                if (msg.axl_text && axlReply) axlReply.textContent = msg.axl_text;
                break;

            case "SECURITY_MODE":
                resetInactivityTimer();
                updateSecurityMode(msg.mode);
                break;

            case "MUTE_STATE":
                resetInactivityTimer();
                updateMuteUI(msg.muted);
                break;

            case "ANIMATION":
            case "VIDEO_ANIMATION":
                resetInactivityTimer();
                if (msg.video) switchAvatarVideo(msg.video);
                break;

            case "MIC_LEVEL":
                // High-frequency audio streaming frames (10-30Hz) - do NOT reset inactivity timer!
                if (typeof msg.level === "number") {
                    targetMicLevel = Math.max(0, Math.min(1, msg.level));
                }
                break;

            default:
                break;
        }
    }

    function escapeHtml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    // ==========================================================================
    // 6. TELEMETRY ENGINE (Live Slave Data + Dynamic Fallback for Browser Tests)
    // ==========================================================================
    function generateFallbackTelemetry() {
        simCpuPercent = Math.max(12, Math.min(82, simCpuPercent + (Math.floor(Math.random() * 11) - 5)));
        const cpuTemp = Math.round(42 + (simCpuPercent / 100) * 24);
        const cpuFreq = Math.round(2500 + (simCpuPercent / 100) * 1300);

        simRamPercent = Math.max(36, Math.min(76, simRamPercent + (Math.floor(Math.random() * 5) - 2)));
        const ramTotal = 8.0;
        const ramUsed = ((simRamPercent / 100) * ramTotal).toFixed(1);
        const ramFree = (ramTotal - parseFloat(ramUsed)).toFixed(1);

        simGpuPercent = Math.max(10, Math.min(72, simGpuPercent + (Math.floor(Math.random() * 9) - 4)));
        const gpuTemp = Math.round(43 + (simGpuPercent / 100) * 20);

        simFanRpm = Math.max(1350, Math.min(2600, Math.round(1480 + (simCpuPercent / 100) * 800 + (Math.random() * 50 - 25))));

        return {
            _is_fallback: true,
            cpu: {
                usage_percent: simCpuPercent,
                temperature_c: cpuTemp,
                freq_mhz: cpuFreq,
                core_count: navigator.hardwareConcurrency || 4
            },
            memory: {
                total_gb: ramTotal,
                used_gb: parseFloat(ramUsed),
                available_gb: parseFloat(ramFree),
                percent: simRamPercent
            },
            gpus: [
                {
                    name: "Integrated / System GPU",
                    type: "integrated",
                    usage_percent: simGpuPercent,
                    temperature_c: gpuTemp,
                    freq_mhz: 950
                }
            ],
            fans: [
                {
                    label: "CPU FAN",
                    rpm: simFanRpm
                }
            ]
        };
    }

    function startTelemetryLoop() {
        stopTelemetryLoop();
        if (Date.now() - lastRealTelemetryTime > 2500) {
            updateTelemetryData(generateFallbackTelemetry(), telemetryTarget);
        }
        telemetrySimInterval = setInterval(() => {
            if (!isTelemetryActive) {
                stopTelemetryLoop();
                return;
            }
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: "REQUEST_TELEMETRY",
                    target: telemetryTarget
                }));
            }
            if (Date.now() - lastRealTelemetryTime > 2500) {
                updateTelemetryData(generateFallbackTelemetry(), telemetryTarget);
            }
        }, 1500);
    }

    function stopTelemetryLoop() {
        if (telemetrySimInterval) {
            clearInterval(telemetrySimInterval);
            telemetrySimInterval = null;
        }
    }

    function setTelemetryMode(enabled, target, notifyServer = true) {
        isTelemetryActive = !!enabled;
        if (target) telemetryTarget = target;

        body.classList.toggle("telemetry-active", isTelemetryActive);

        if (telemetryPanel) {
            telemetryPanel.hidden = !isTelemetryActive;
            telemetryPanel.style.display = isTelemetryActive ? "flex" : "none";
        }

        const isServer = (telemetryTarget === "server" || telemetryTarget === "master");

        // Mutually exclusive button states: only the active node's button is highlighted
        if (serverTelemBtn) {
            const active = isTelemetryActive && isServer;
            serverTelemBtn.classList.toggle("active", active);
            serverTelemBtn.textContent = active ? "SERVER TELEM: ON" : "SERVER TELEM: OFF";
        }

        if (slaveTelemBtn) {
            const active = isTelemetryActive && !isServer;
            slaveTelemBtn.classList.toggle("active", active);
            slaveTelemBtn.textContent = active ? "SLAVE TELEM: ON" : "SLAVE TELEM: OFF";
        }

        // Update panel titles and badge
        if (isTelemetryActive) {
            if (telemTitle) {
                telemTitle.textContent = isServer ? "SERVER TELEMETRY" : "SLAVE TELEMETRY";
            }
            if (telemNodeBadge) {
                telemNodeBadge.textContent = isServer
                    ? "NODE: MASTER // LIVE"
                    : `NODE: [${telemetryTarget.toUpperCase()}] // LIVE`;
            }
            startTelemetryLoop();
        } else {
            stopTelemetryLoop();
        }

        if (notifyServer && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "TOGGLE_TELEMETRY",
                enabled: isTelemetryActive,
                target: telemetryTarget
            }));
        }

        // Smoothly resize visor canvas as layout transitions between centered and split view
        setTimeout(resizeCanvas, 30);
        setTimeout(resizeCanvas, 200);
        setTimeout(resizeCanvas, 400);
    }

    function updateTelemetryData(data, target) {
        if (!data) return;
        const tgt = target || telemetryTarget || "slave";
        if (!data._is_fallback) {
            lastRealTelemetryTime = Date.now();
        }

        const isServer = (tgt === "server" || tgt === "master" || telemetryTarget === "server");

        if (telemNodeBadge) {
            telemNodeBadge.className = "badge badge-telem-online";
            telemNodeBadge.textContent = isServer
                ? "NODE: MASTER // LIVE"
                : `NODE: [${tgt.toUpperCase()}] // LIVE`;
        }

        if (telemTitle) {
            telemTitle.textContent = isServer ? "SERVER TELEMETRY" : "SLAVE TELEMETRY";
        }

        // 1. CPU
        const cpu = data.cpu || {};
        const cpuUsage = Math.round(cpu.usage_percent || 0);
        if (telemCpuUsage) telemCpuUsage.textContent = `${cpuUsage}%`;
        if (telemCpuBar) {
            telemCpuBar.style.width = `${Math.min(100, Math.max(0, cpuUsage))}%`;
            if (cpuUsage >= 85) telemCpuBar.className = "telem-meter-fill crit";
            else if (cpuUsage >= 70) telemCpuBar.className = "telem-meter-fill warn";
            else telemCpuBar.className = "telem-meter-fill";
        }

        let resolvedCpuTemp = cpu.temperature_c;
        if (resolvedCpuTemp === undefined || resolvedCpuTemp === null) {
            const rawTemps = data.temperatures || {};
            for (const k of ["coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz"]) {
                if (rawTemps[k] && rawTemps[k].length > 0 && rawTemps[k][0].current) {
                    resolvedCpuTemp = Math.round(rawTemps[k][0].current);
                    break;
                }
            }
        }
        if (telemCpuTemp) {
            telemCpuTemp.textContent = (resolvedCpuTemp !== null && resolvedCpuTemp !== undefined)
                ? `${Math.round(resolvedCpuTemp)}°C` : "44°C";
        }
        if (telemCpuFreq) {
            telemCpuFreq.textContent = cpu.freq_mhz ? `${Math.round(cpu.freq_mhz)} MHz` : "-- MHz";
        }
        if (telemCpuCores) {
            telemCpuCores.textContent = `${cpu.core_count || 4} Cores`;
        }

        // 2. RAM
        const mem = data.memory || {};
        const ramUsed = (mem.used_gb || 0).toFixed(1);
        const ramTot = (mem.total_gb || 0).toFixed(1);
        const ramPct = Math.round(mem.percent || 0);
        const ramFree = (mem.available_gb || 0).toFixed(1);

        if (telemRamUsed) telemRamUsed.textContent = ramUsed;
        if (telemRamTotal) telemRamTotal.textContent = `/ ${ramTot} GB`;
        if (telemRamPct) telemRamPct.textContent = `${ramPct}%`;
        if (telemRamFree) telemRamFree.textContent = `${ramFree} GB`;
        if (telemRamBar) {
            telemRamBar.style.width = `${Math.min(100, Math.max(0, ramPct))}%`;
            if (ramPct >= 85) telemRamBar.className = "telem-meter-fill crit";
            else if (ramPct >= 70) telemRamBar.className = "telem-meter-fill warn";
            else telemRamBar.className = "telem-meter-fill";
        }

        // 3. GPU(s)
        const gpus = data.gpus || [];
        if (telemGpuList) {
            if (gpus.length === 0) {
                const fallbackLoad = Math.max(8, Math.round(cpuUsage * 0.45));
                telemGpuList.innerHTML = `
                    <div class="telem-gpu-item">
                        <div class="telem-gpu-top">
                            <span class="telem-gpu-name">Integrated Graphics</span>
                            <span class="telem-gpu-badge">INTEGRATED // ${fallbackLoad}%</span>
                        </div>
                        <div class="telem-meter-track">
                            <div class="telem-meter-fill" style="width: ${fallbackLoad}%;"></div>
                        </div>
                    </div>`;
                if (telemGpuGlobalTemp) {
                    telemGpuGlobalTemp.textContent = (resolvedCpuTemp !== null && resolvedCpuTemp !== undefined)
                        ? `${Math.round(resolvedCpuTemp)}°C` : "44°C";
                }
            } else {
                let html = "";
                let maxTemp = null;
                gpus.forEach((gpu) => {
                    const isDed = gpu.type === "dedicated";
                    const typeTag = isDed ? "DEDICATED" : "INTEGRATED";
                    const gUsage = Math.round(gpu.usage_percent || 0);
                    const tempStr = (gpu.temperature_c !== null && gpu.temperature_c !== undefined)
                        ? `${Math.round(gpu.temperature_c)}°C`
                        : (gpu.freq_mhz ? `${Math.round(gpu.freq_mhz)} MHz` : `${gUsage}%`);

                    if (gpu.temperature_c !== null && gpu.temperature_c !== undefined) {
                        if (maxTemp === null || gpu.temperature_c > maxTemp) maxTemp = gpu.temperature_c;
                    }

                    let vramInfo = "";
                    if (gpu.memory_used_mb && gpu.memory_total_mb) {
                        vramInfo = `<div class="telem-gpu-vram">VRAM: ${Math.round(gpu.memory_used_mb)} / ${Math.round(gpu.memory_total_mb)} MB</div>`;
                    }

                    html += `
                    <div class="telem-gpu-item">
                        <div class="telem-gpu-top">
                            <span class="telem-gpu-name">${escapeHtml(gpu.name || 'GPU')}</span>
                            <span class="telem-gpu-badge">${typeTag} // ${tempStr}</span>
                        </div>
                        <div class="telem-meter-track">
                            <div class="telem-meter-fill" style="width: ${Math.min(100, Math.max(0, gUsage))}%;"></div>
                        </div>
                        ${vramInfo}
                    </div>`;
                });
                telemGpuList.innerHTML = html;
                if (telemGpuGlobalTemp) {
                    telemGpuGlobalTemp.textContent = maxTemp !== null ? `${Math.round(maxTemp)}°C` : "45°C";
                }
            }
        }

        // 4. Fans
        const fans = data.fans || [];
        if (fans.length > 0) {
            const f = fans[0];
            if (telemFanRpm) telemFanRpm.textContent = f.rpm;
            if (telemFanLabel) telemFanLabel.textContent = (f.label || "CPU FAN").toUpperCase();
            if (telemFanStatus) {
                telemFanStatus.textContent = f.rpm > 0 ? "ACTIVE" : "IDLE";
                telemFanStatus.className = "telem-temp-badge";
            }
        } else {
            const estRpm = Math.round(1450 + cpuUsage * 8);
            if (telemFanRpm) telemFanRpm.textContent = estRpm;
            if (telemFanLabel) telemFanLabel.textContent = "SYSTEM COOLING";
            if (telemFanStatus) {
                telemFanStatus.textContent = "AUTO";
                telemFanStatus.className = "telem-temp-badge";
            }
        }
    }

    // --- Interactive Telemetry Buttons (Mouse + Touch) ---
    // Rule: Mutually exclusive - only one telemetry panel can be open at a time; clicking one closes the other.
    if (serverTelemBtn) {
        serverTelemBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (isTelemetryActive && (telemetryTarget === "server" || telemetryTarget === "master")) {
                setTelemetryMode(false, "server", true);
            } else {
                setTelemetryMode(true, "server", true);
            }
        });
    }

    if (slaveTelemBtn) {
        slaveTelemBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (isTelemetryActive && telemetryTarget !== "server" && telemetryTarget !== "master") {
                setTelemetryMode(false, "slave", true);
            } else {
                setTelemetryMode(true, "slave", true);
            }
        });
    }

    if (telemCloseBtn) {
        telemCloseBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            setTelemetryMode(false, telemetryTarget, true);
        });
    }

    // ==========================================================================
    // 7. ONE-WAY SECURITY MODE CONTROLLER (Clickable ONLY when on FRIENDLY)
    // ==========================================================================
    function closeModePopup() {
        if (modeSelectorPopup) {
            modeSelectorPopup.classList.remove("open");
            modeSelectorPopup.hidden = true;
        }
    }

    function updateAskButton() {
        if (!askBtn) return;
        askBtn.classList.remove("ask-friendly", "ask-restrict", "ask-paranoid");
        askBtn.classList.add(`ask-${securityMode}`);
        const listening = currentState === "LISTENING";
        askBtn.classList.toggle("listening", listening);
        askBtn.disabled = currentState !== "IDLE";
        if (askBtnLabel) askBtnLabel.textContent = listening ? "LISTENING..." : "ASK ME...";
        askBtn.title = securityMode === "friendly"
            ? "Fai una domanda senza dire la wake-word"
            : "Fai una domanda senza wake-word (verifica vocale ancora attiva)";
    }

    if (askBtn) {
        askBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (askBtn.disabled) return;
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ASK_START" }));
            } else {
                // UI Preview Simulation Fallback (when testing offline/standalone)
                setVisorState("LISTENING");
                let simTick = 0;
                const simInterval = setInterval(() => {
                    targetMicLevel = (simTick % 2 === 0) ? 0.8 : 0.2;
                    simTick++;
                    if (simTick > 7) {
                        clearInterval(simInterval);
                        targetMicLevel = 0.0;
                        setVisorState("THINKING");
                        setTimeout(() => {
                            setVisorState("SPEAKING");
                            setTimeout(() => {
                                setVisorState("IDLE");
                            }, 2000);
                        }, 1800);
                    }
                }, 350);
            }
        });
    }

    function updateSecurityMode(mode) {
        securityMode = (mode || "friendly").toLowerCase().trim();
        closeModePopup();
        updateAskButton();

        if (!secModeBadge) return;

        if (securityMode === "friendly") {
            secModeBadge.className = "badge badge-friendly mode-unlocked";
            secModeBadge.textContent = "MODE: FRIENDLY ▾";
            secModeBadge.title = "Clicca per bloccare la sicurezza su Restrict o Paranoid";
        } else {
            secModeBadge.className = `badge badge-${securityMode} mode-locked`;
            secModeBadge.textContent = `🔒 MODE: ${securityMode.toUpperCase()}`;
            secModeBadge.title = "Modalità bloccata. Solo l'Owner tramite verifica vocale può sbloccare il sistema.";
        }
    }

    if (secModeBadge) {
        secModeBadge.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();

            // Security Rule: Mode can ONLY be clicked when currently in 'friendly' mode
            if (securityMode !== "friendly") {
                if (axlReply) {
                    axlReply.textContent = `Sicurezza bloccata su ${securityMode.toUpperCase()}. Solo l'Owner con verifica vocale può modificare questa modalità.`;
                }
                return;
            }

            if (modeSelectorPopup) {
                const isOpen = modeSelectorPopup.classList.contains("open");
                if (isOpen) {
                    closeModePopup();
                } else {
                    modeSelectorPopup.hidden = false;
                    modeSelectorPopup.classList.add("open");
                }
            }
        });
    }

    document.querySelectorAll(".mode-option-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const targetMode = btn.getAttribute("data-mode");
            if (securityMode === "friendly" && (targetMode === "restrict" || targetMode === "paranoid")) {
                updateSecurityMode(targetMode);
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: "SET_SECURITY_MODE",
                        mode: targetMode
                    }));
                }
                if (axlReply) {
                    axlReply.textContent = `Modalità ${targetMode.toUpperCase()} attivata e bloccata. Verifica biometrica vocale attiva.`;
                }
            }
        });
    });

    document.addEventListener("click", (e) => {
        if (modeSelectorPopup && modeSelectorPopup.classList.contains("open")) {
            if (!modeSelectorPopup.contains(e.target) && e.target !== secModeBadge) {
                closeModePopup();
            }
        }
    });

    // ==========================================================================
    // 8. TACTICAL MUTE CONTROLLER (Physical / UI Button Unlock Requirement)
    // ==========================================================================
    function updateMuteUI(muted) {
        isMuted = !!muted;
        updateAskButton();
        if (!muteBtn) return;

        muteBtn.classList.toggle("muted", isMuted);
        if (isMuted) {
            if (muteBtnIcon) muteBtnIcon.textContent = "🔇";
            if (muteBtnLabel) muteBtnLabel.textContent = "MIC: MUTO (CLICCA PER SBLOCCARE)";
            muteBtn.title = "Microfono disattivato. Clicca per riattivare l'ascolto vocale.";
            if (axlReply && currentState !== "SPEAKING") {
                axlReply.textContent = "Modalità muto attiva. Sensori audio disattivati (sblocco consentito solo tramite questo pulsante).";
            }
        } else {
            if (muteBtnIcon) muteBtnIcon.textContent = "🎙️";
            if (muteBtnLabel) muteBtnLabel.textContent = "MIC: ATTIVO";
            muteBtn.title = "Disattiva ascolto microfono (Può essere riattivato solo cliccando qui)";
        }
    }

    if (muteBtn) {
        muteBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const newMuted = !isMuted;
            updateMuteUI(newMuted);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: "TOGGLE_MUTE",
                    muted: newMuted
                }));
            }
        });
    }

    // ==========================================================================
    // 9. BIOMETRICS & VISOR STATE CONTROLLER
    // ==========================================================================
    function updateBiometrics(data) {
        const id = (data.identity || "UNKNOWN").toUpperCase();
        const score = typeof data.score === "number" ? data.score : 0.0;
        const auth = !!data.authorized;

        if (bioIdentity) bioIdentity.textContent = id;
        if (bioScoreText) bioScoreText.textContent = score.toFixed(3);
        const percent = Math.min(100, Math.max(0, Math.round(score * 100)));
        if (bioScoreBar) bioScoreBar.style.width = `${percent}%`;

        if (bioAuthStatus) {
            bioAuthStatus.className = "bio-value";
            if (id === "OWNER") {
                bioAuthStatus.classList.add("auth-owner");
                bioAuthStatus.textContent = "VERIFIED // OWNER";
            } else if (auth) {
                bioAuthStatus.classList.add("auth-friend");
                bioAuthStatus.textContent = "AUTHORIZED // FRIEND";
            } else {
                bioAuthStatus.classList.add("auth-stranger");
                bioAuthStatus.textContent = "DENIED // STRANGER";
                if (securityMode !== "friendly") {
                    setVisorState("ALERT", "SECURITY BREACH", "UNAUTHORIZED SPEAKER ACCESS");
                    setTimeout(() => {
                        if (currentState === "ALERT") setVisorState("IDLE");
                    }, 4000);
                }
            }
        }
    }

    function setVisorState(state, customLabel, customSub) {
        currentState = (state || "IDLE").toUpperCase();

        // Preserve non-state classes like 'telemetry-active'
        body.classList.remove("state-idle", "state-listening", "state-thinking", "state-speaking", "state-alert");
        body.classList.add(`state-${currentState.toLowerCase()}`);

        if (currentState === "IDLE") {
            // When returning to IDLE standby, start/re-arm the subdued dimming timer
            resetInactivityTimer();
        } else {
            // Any active interaction (LISTENING, THINKING, SPEAKING, ALERT) wakes the display immediately
            wakeScreen();
            if (idleInactivityTimer) {
                clearTimeout(idleInactivityTimer);
                idleInactivityTimer = null;
            }
        }

        const defaults = {
            IDLE: {
                label: "STANDBY // PASSIVE SURVEILLANCE",
                sub: "ACOUSTIC ARRAY: ACTIVE (16kHz PCM)"
            },
            LISTENING: {
                label: "ACOUSTIC SENSORS ACTIVE // LISTENING",
                sub: "VOICE STREAM INGESTION IN PROGRESS"
            },
            THINKING: {
                label: "NEURAL CORE // COGNITIVE INFERENCE",
                sub: "MULTIMODAL DISPATCH & FUNCTION CALLING"
            },
            SPEAKING: {
                label: "VOCAL TRANSMISSION // STREAMING PCM",
                sub: "NEURAL TTS ENGINE ACTIVE"
            },
            ALERT: {
                label: "ALERT // UNAUTHORIZED ENTITY DETECTED",
                sub: "BIOMETRIC REJECTION PROTOCOL ACTIVE"
            }
        };

        const config = defaults[currentState] || defaults["IDLE"];
        if (stateLabel) stateLabel.textContent = customLabel || config.label;
        if (stateSub) stateSub.textContent = customSub || config.sub;
        if (currentState !== "LISTENING" && currentState !== "IDLE") {
            targetMicLevel = 0.0;
        }
        updateAskButton();
    }

    // ==========================================================================
    // 9. CYBERNETIC VIDEO AVATAR ENGINE (A-X-L NEON ROBOTIC DOG)
    // ==========================================================================
    function initVideoAvatar() {
        if (!visorVideo) return;
        visorVideo.playsInline = true;
        visorVideo.muted = true;
        visorVideo.loop = true;

        const attemptPlay = () => {
            const p = visorVideo.play();
            if (p !== undefined) {
                p.catch(() => {
                    // Browser policy fallback: play on first user interaction
                    const onFirstUserAction = () => {
                        visorVideo.play().catch(() => {});
                        window.removeEventListener("pointerdown", onFirstUserAction);
                        window.removeEventListener("keydown", onFirstUserAction);
                    };
                    window.addEventListener("pointerdown", onFirstUserAction, { once: true });
                    window.addEventListener("keydown", onFirstUserAction, { once: true });
                });
            }
        };
        attemptPlay();
    }

    function switchAvatarVideo(videoSrc) {
        if (!visorVideo || !videoSrc) return;
        const currentSrc = visorVideo.getAttribute("data-current-src") || "";
        if (currentSrc === videoSrc) return;
        visorVideo.setAttribute("data-current-src", videoSrc);
        const source = visorVideo.querySelector("source");
        if (source) {
            source.src = videoSrc;
        } else {
            visorVideo.src = videoSrc;
        }
        visorVideo.load();
        visorVideo.play().catch(() => {});
    }

    // ==========================================================================
    // 10. 60FPS PROCEDURAL VISOR HUD CANVAS RENDERER (Fallback/Overlay)
    // ==========================================================================
    function renderVisor() {
        if (!canvas || !ctx || canvas.style.display === "none") {
            return;
        }

        const dpr = window.devicePixelRatio || 1;
        const width = canvas.width / dpr;
        const height = canvas.height / dpr;
        const cx = width / 2;
        const cy = height / 2;
        const maxRadius = Math.min(cx, cy) * 0.85;

        ctx.clearRect(0, 0, width, height);
        animTime += 0.02;

        let primaryColor = "#00f0ff";
        let secondaryColor = "rgba(0, 240, 255, 0.2)";
        let glowColor = "rgba(0, 240, 255, 0.5)";

        if (currentState === "LISTENING") {
            primaryColor = "#0088ff";
            secondaryColor = "rgba(0, 136, 255, 0.3)";
            glowColor = "rgba(0, 136, 255, 0.7)";
        } else if (currentState === "THINKING") {
            primaryColor = "#b038ff";
            secondaryColor = "rgba(176, 56, 255, 0.25)";
            glowColor = "rgba(176, 56, 255, 0.7)";
        } else if (currentState === "SPEAKING") {
            primaryColor = "#00e5ff";
            secondaryColor = "rgba(0, 229, 255, 0.3)";
            glowColor = "rgba(0, 229, 255, 0.8)";
        } else if (currentState === "ALERT") {
            primaryColor = "#ff003b";
            secondaryColor = "rgba(255, 0, 59, 0.35)";
            glowColor = "rgba(255, 0, 59, 0.9)";
        }

        ctx.save();
        ctx.shadowBlur = 10;
        ctx.shadowColor = glowColor;

        // --- LAYER 1: Outer Segmented Rotating Ring ---
        const outerRotSpeed = currentState === "THINKING" ? animTime * 1.5 : animTime * 0.3;
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(outerRotSpeed);
        ctx.strokeStyle = secondaryColor;
        ctx.lineWidth = 2;
        ctx.setLineDash([12, 8, 4, 8]);
        ctx.beginPath();
        ctx.arc(0, 0, maxRadius, 0, Math.PI * 2);
        ctx.stroke();

        for (let i = 0; i < 4; i++) {
            const angle = (i * Math.PI) / 2;
            const tx1 = Math.cos(angle) * (maxRadius - 8);
            const ty1 = Math.sin(angle) * (maxRadius - 8);
            const tx2 = Math.cos(angle) * (maxRadius + 8);
            const ty2 = Math.sin(angle) * (maxRadius + 8);
            ctx.strokeStyle = primaryColor;
            ctx.lineWidth = 3;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(tx1, ty1);
            ctx.lineTo(tx2, ty2);
            ctx.stroke();
        }
        ctx.restore();

        // --- LAYER 2: Middle Concentric Radar / Wave Ring ---
        const midRadius = maxRadius * 0.72;
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(-animTime * 0.5);
        ctx.strokeStyle = primaryColor;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([20, 15]);
        ctx.beginPath();
        ctx.arc(0, 0, midRadius, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();

        // --- LAYER 3: Dynamic State Core Waveform / Equalizer ---
        const coreRadius = maxRadius * 0.42;

        if (currentState === "SPEAKING") {
            const numBars = 32;
            for (let i = 0; i < numBars; i++) {
                const angle = (i / numBars) * Math.PI * 2 + animTime * 0.5;
                const barHeight = 8 + Math.abs(Math.sin(animTime * 4 + i * 0.5)) * 36;
                const r1 = coreRadius;
                const r2 = coreRadius + barHeight;

                ctx.strokeStyle = primaryColor;
                ctx.lineWidth = 3;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(cx + Math.cos(angle) * r1, cy + Math.sin(angle) * r1);
                ctx.lineTo(cx + Math.cos(angle) * r2, cy + Math.sin(angle) * r2);
                ctx.stroke();
            }
        } else if (currentState === "LISTENING") {
            const pulse = (animTime * 2) % 1;
            for (let i = 0; i < 3; i++) {
                const p = (pulse + i * 0.33) % 1;
                ctx.strokeStyle = primaryColor;
                ctx.globalAlpha = 1 - p;
                ctx.lineWidth = 2;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.arc(cx, cy, coreRadius + p * 40, 0, Math.PI * 2);
                ctx.stroke();
            }
            ctx.globalAlpha = 1.0;
        } else if (currentState === "THINKING") {
            const numNodes = 6;
            for (let i = 0; i < numNodes; i++) {
                const angle = (i / numNodes) * Math.PI * 2 + animTime * 2.5;
                const nx = cx + Math.cos(angle) * (coreRadius * 0.85);
                const ny = cy + Math.sin(angle) * (coreRadius * 0.85);
                ctx.fillStyle = primaryColor;
                ctx.beginPath();
                ctx.arc(nx, ny, 4, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        // Central Cybernetic Hound Iris / Core Dot
        const corePulse = Math.sin(animTime * 2) * 4;
        ctx.fillStyle = primaryColor;
        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(4, coreRadius * 0.35 + corePulse), 0, Math.PI * 2);
        ctx.fill();

        ctx.restore();
        animFrameId = requestAnimationFrame(renderVisor);
    }

    // ==========================================================================
    // 11. REAL-TIME AUDIO-REACTIVE AURA ENGINE (Subtle Dynamic Microphone VU)
    // ==========================================================================
    function startAudioReactiveLoop() {
        function stepAudioGlow() {
            // Fast attack (~0.35) for instant vocal response, gentle decay (~0.12) to avoid jitter
            const speed = targetMicLevel > currentMicLevel ? 0.35 : 0.12;
            currentMicLevel += (targetMicLevel - currentMicLevel) * speed;
            if (currentMicLevel < 0.001) {
                currentMicLevel = 0.0;
            }

            // Continuous natural decay towards zero
            targetMicLevel *= 0.88;

            document.documentElement.style.setProperty("--mic-level", currentMicLevel.toFixed(3));
            requestAnimationFrame(stepAudioGlow);
        }
        requestAnimationFrame(stepAudioGlow);
    }

    // Start everything
    connectWebSocket();
    initVideoAvatar();
    renderVisor();
    startAudioReactiveLoop();
    resetInactivityTimer();

})();
