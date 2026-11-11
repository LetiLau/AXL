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
    const brandNodeTag = document.getElementById("brand-node-tag");
    const secModeBadge = document.getElementById("sec-mode-badge");
    const modeSelectorPopup = document.getElementById("mode-selector-popup");
    const connStatus = document.getElementById("connection-status");
    const connStatusText = document.getElementById("connection-status-text");
    const connBadge = connStatus; // alias for backwards compatibility
    
    // Unified Telemetry Selector Elements
    const telemBtn = document.getElementById("telem-btn");
    const telemPopup = document.getElementById("telem-popup");
    const telemOptServer = document.getElementById("telem-opt-server");
    const telemOptSlave = document.getElementById("telem-opt-slave");
    const telemOptKiosk = document.getElementById("telem-opt-kiosk");
    const telemOptClose = document.getElementById("telem-opt-close");

    // Legacy button aliases for backwards compatibility
    const serverTelemBtn = telemOptServer;
    const slaveTelemBtn = telemOptSlave;
    const kioskTelemBtn = telemOptKiosk;
    const telemetryBtn = telemBtn;

    // Primary Telemetry HUD Panel (Left)
    const telemetryPanel = document.getElementById("telemetry-panel");
    const telemCloseBtn = document.getElementById("telem-close-btn");
    const telemTitle = document.getElementById("telem-title");
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
    const telemFanUnit = document.getElementById("telem-fan-unit");
    const telemFanStatus = document.getElementById("telem-fan-status");
    const telemFanLabel = document.getElementById("telem-fan-label");
    const telemFanSubval = document.getElementById("telem-fan-subval");
    const telemFanCardTitle = document.getElementById("telem-fan-card-title");
    const telemCpuCardTitle = document.getElementById("telem-cpu-card-title");
    const telemCpuSub1Lbl = document.getElementById("telem-cpu-sub1-lbl");
    const telemCpuSub2Lbl = document.getElementById("telem-cpu-sub2-lbl");
    const telemRamCardTitle = document.getElementById("telem-ram-card-title");
    const telemGpuCardTitle = document.getElementById("telem-gpu-card-title");

    // Master Server Mini Sparklines Elements (Sub-Header Sliders)
    const serverMiniTelem = document.getElementById("server-mini-telem");
    const miniCpuFill = document.getElementById("mini-cpu-fill");
    const miniCpuTemp = document.getElementById("mini-cpu-temp");
    const miniGpuFill = document.getElementById("mini-gpu-fill");
    const miniMemFill = document.getElementById("mini-mem-fill");

    const stateLabel = document.getElementById("visor-state-label");
    const stateSub = document.getElementById("visor-sub-label");
    const bioIdentity = document.getElementById("bio-identity");
    const bioScoreBar = document.getElementById("bio-score-bar");
    const bioScoreText = document.getElementById("bio-score-text");
    const bioAuthStatus = document.getElementById("bio-auth-status");
    const userTranscript = document.getElementById("user-transcript");
    const axlReply = document.getElementById("axl-reply");
    const fullscreenBtn = document.getElementById("fullscreen-btn");
    const settingsBtn = document.getElementById("settings-btn");
    const settingsPopup = document.getElementById("settings-popup");
    const settingOptReload = document.getElementById("setting-opt-reload");
    const settingOptIp = document.getElementById("setting-opt-ip");
    const settingOptReconnect = document.getElementById("setting-opt-reconnect");
    const settingOptLang = document.getElementById("setting-opt-lang");
    const settingOptKillKiosk = document.getElementById("setting-opt-kill-kiosk");
    const muteBtn = document.getElementById("mute-btn");
    const muteBtnIcon = document.getElementById("mute-btn-icon");
    const muteBtnLabel = document.getElementById("mute-btn-label");
    const askBtn = document.getElementById("ask-btn");
    const askBtnIcon = document.getElementById("ask-btn-icon");
    const askBtnLabel = document.getElementById("ask-btn-label");

    // --- State Variables ---
    let currentState = "IDLE";
    let securityMode = "friendly";
    let isMuted = false;
    let isTelemetryActive = false;
    let telemetryTarget = "slave";
    let telemetrySimInterval = null;
    let lastRealTelemetryTime = 0;
    let serverMiniTelemInterval = null;
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

    // Dynamic text scaler for A-X-L replies (prevents subtitle overflow on Galaxy A33 / mobile kiosk)
    function fitAxlReplyText(text) {
        if (!axlReply) return;
        const clean = (text || "").trim();
        axlReply.textContent = clean;
        axlReply.classList.remove("reply-sm", "reply-xs", "reply-xxs");
        const len = clean.length;
        if (len > 220) {
            axlReply.classList.add("reply-xxs");
        } else if (len > 130) {
            axlReply.classList.add("reply-xs");
        } else if (len > 70) {
            axlReply.classList.add("reply-sm");
        }
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
    // 3. FULLSCREEN & RELOAD CONTROLLERS + CLIENT ENVIRONMENT DETECTION
    // ==========================================================================
    function detectClientEnvironment() {
        const ua = navigator.userAgent || "";
        const isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
        const isMobileUa = /Android|iPhone|iPad|iPod|Mobile/i.test(ua);
        const isAndroidWv = /;\s*wv\s*\)/i.test(ua) || (ua.includes("Version/4.0") && isMobileUa);
        const isKiosk = Boolean(
            (window.AXLKiosk && typeof window.AXLKiosk.exitKiosk === "function") ||
            ua.includes("AXL-Kiosk") ||
            (new URLSearchParams(window.location.search).get("client") === "kiosk")
        );
        const isTablet = !isKiosk && (
            (/iPad|Tablet|PlayBook/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) || (/Android/i.test(ua) && !/Mobile/i.test(ua))) ||
            (isTouch && Math.min(window.innerWidth, window.innerHeight) >= 600 && Math.max(window.innerWidth, window.innerHeight) >= 900)
        );
        const isMobilePhone = !isTablet && !isKiosk && (isMobileUa || (isTouch && (window.innerWidth <= 899 || window.innerHeight <= 520)));

        let clientType = "desktop";
        if (isKiosk || isAndroidWv) {
            clientType = "kiosk";
        } else if (isTablet) {
            clientType = "tablet";
        } else if (isMobilePhone) {
            clientType = "mobile";
        } else {
            clientType = "desktop";
        }

        body.classList.remove("client-desktop", "client-mobile", "client-kiosk", "client-tablet");
        body.classList.add(`client-${clientType}`);
        if (clientType === "tablet") body.classList.add("client-desktop");
        document.documentElement.classList.remove("client-desktop", "client-mobile", "client-kiosk", "client-tablet");
        document.documentElement.classList.add(`client-${clientType}`);
        if (clientType === "tablet") document.documentElement.classList.add("client-desktop");

        console.log(`[HUD] Client environment detected: ${clientType.toUpperCase()} (UA: ${ua})`);
        return {
            clientType,
            isKiosk: clientType === "kiosk",
            isTablet: clientType === "tablet",
            isMobile: clientType === "mobile" || clientType === "kiosk",
            isDesktop: clientType === "desktop" || clientType === "tablet",
            isTouch
        };
    }
    const clientEnv = detectClientEnvironment();
    window.axlClient = clientEnv;

    function updateBrandNodeTag(assignedTag) {
        if (!brandNodeTag) return;
        if (assignedTag) {
            brandNodeTag.textContent = assignedTag;
            return;
        }
        const isKiosk = Boolean(
            (window.AXLKiosk && typeof window.AXLKiosk.exitKiosk === "function") ||
            (window.axlClient && window.axlClient.isKiosk) ||
            (navigator.userAgent && navigator.userAgent.includes("AXL-Kiosk"))
        );
        brandNodeTag.textContent = isKiosk ? "KIOSK" : "NODE-..";
    }

    function updateKioskKillButton() {
        if (!settingOptKillKiosk) return;
        const isAndroidKiosk = Boolean(
            (window.AXLKiosk && typeof window.AXLKiosk.exitKiosk === "function") ||
            (window.axlClient && window.axlClient.isKiosk) ||
            (navigator.userAgent && navigator.userAgent.includes("AXL-Kiosk"))
        );
        if (isAndroidKiosk) {
            settingOptKillKiosk.disabled = false;
            settingOptKillKiosk.classList.remove("disabled-muted");
            settingOptKillKiosk.title = "Arresta l'app Kiosk e rilascia subito il microfono in background";
        } else {
            settingOptKillKiosk.disabled = true;
            settingOptKillKiosk.classList.add("disabled-muted");
            settingOptKillKiosk.title = "Disponibile solo all'interno dell'app Android Kiosk (per arrestare microfono e processo)";
        }
    }

    function updateKioskUI() {
        const isKiosk = Boolean(
            (window.AXLKiosk && typeof window.AXLKiosk.exitKiosk === "function") ||
            (window.axlClient && window.axlClient.isKiosk) ||
            (navigator.userAgent && navigator.userAgent.includes("AXL-Kiosk"))
        );

        if (telemOptKiosk) {
            if (isKiosk) {
                telemOptKiosk.disabled = false;
                telemOptKiosk.classList.remove("disabled-muted");
                telemOptKiosk.title = "Visualizza telemetria hardware del dispositivo Android Kiosk (A33)";
            } else {
                telemOptKiosk.disabled = true;
                telemOptKiosk.classList.add("disabled-muted");
                telemOptKiosk.title = "Disponibile solo all'interno dell'app Android Kiosk (disattivato da browser PC)";
            }
        }

        updateKioskKillButton();
        updateBrandNodeTag();

        if (fullscreenBtn) {
            fullscreenBtn.style.display = isKiosk ? "none" : "";
        }
    }
    updateKioskUI();

    if (fullscreenBtn) {
        fullscreenBtn.addEventListener("click", () => {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(() => {});
            } else {
                document.exitFullscreen().catch(() => {});
            }
        });
    }

    function closeSettingsPopup() {
        if (settingsPopup) {
            settingsPopup.classList.remove("open");
            settingsPopup.hidden = true;
        }
        if (settingsBtn) {
            settingsBtn.classList.remove("open");
        }
    }

    function toggleSettingsPopup() {
        if (!settingsPopup) return;
        const isOpen = settingsPopup.classList.contains("open");
        if (isOpen) {
            closeSettingsPopup();
        } else {
            closeModePopup();
            closeTelemPopup();
            updateKioskUI();
            settingsPopup.hidden = false;
            settingsPopup.classList.add("open");
            if (settingsBtn) settingsBtn.classList.add("open");
        }
    }

    if (settingsBtn) {
        settingsBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleSettingsPopup();
        });
    }

    if (settingOptReload) {
        settingOptReload.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeSettingsPopup();
            if (window.AXLKiosk && typeof window.AXLKiosk.reload === "function") {
                window.AXLKiosk.reload();
            } else {
                window.location.reload(true);
            }
        });
    }

    if (settingOptIp) {
        settingOptIp.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeSettingsPopup();
            if (window.AXLKiosk && typeof window.AXLKiosk.showIpConfig === "function") {
                window.AXLKiosk.showIpConfig();
            } else {
                const currentHost = window.location.host;
                const newHost = prompt("Configura Host / Indirizzo IP del Master Server:", currentHost);
                if (newHost && newHost.trim() && newHost.trim() !== currentHost) {
                    window.location.href = `${window.location.protocol}//${newHost.trim()}/ui`;
                }
            }
        });
    }

    if (settingOptReconnect) {
        settingOptReconnect.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeSettingsPopup();
            connectWebSocket();
            if (axlReply && currentState !== "SPEAKING") {
                fitAxlReplyText("Tentativo di riconnessione WebSocket in corso...");
            }
        });
    }

    if (settingOptLang) {
        settingOptLang.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeSettingsPopup();
            if (axlReply && currentState !== "SPEAKING") {
                fitAxlReplyText("Lingua interfaccia: ENG (Default). Switch ITA/ENG attivo nelle prossime versioni.");
            }
        });
    }

    if (settingOptKillKiosk) {
        settingOptKillKiosk.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (settingOptKillKiosk.disabled) return;
            closeSettingsPopup();
            if (window.AXLKiosk && typeof window.AXLKiosk.exitKiosk === "function") {
                window.AXLKiosk.exitKiosk();
            }
        });
    }

    document.addEventListener("click", (e) => {
        if (settingsPopup && settingsPopup.classList.contains("open")) {
            if (!settingsPopup.contains(e.target) && e.target !== settingsBtn) {
                closeSettingsPopup();
            }
        }
    });

    // ==========================================================================
    // 4. WEBSOCKET HUD CLIENT
    // ==========================================================================
    function requestServerMiniTelemetry() {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "REQUEST_SERVER_TELEMETRY" }));
        }
    }

    function startServerMiniTelemLoop() {
        if (serverMiniTelemInterval) clearInterval(serverMiniTelemInterval);
        requestServerMiniTelemetry();
        serverMiniTelemInterval = setInterval(() => {
            requestServerMiniTelemetry();
        }, 1000);
    }

    function stopServerMiniTelemLoop() {
        if (serverMiniTelemInterval) {
            clearInterval(serverMiniTelemInterval);
            serverMiniTelemInterval = null;
        }
    }

    function connectWebSocket() {
        if (wsReconnectTimeout) clearTimeout(wsReconnectTimeout);

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const host = window.location.host || "127.0.0.1:8000";
        const isKioskClient = Boolean(
            (window.AXLKiosk && typeof window.AXLKiosk.exitKiosk === "function") ||
            (window.axlClient && window.axlClient.isKiosk) ||
            (navigator.userAgent && navigator.userAgent.includes("AXL-Kiosk"))
        );
        const wsUrl = `${protocol}//${host}/ws/ui?client=${isKioskClient ? "kiosk" : "browser"}`;

        updateConnectionStatus("connecting");

        try {
            ws = new WebSocket(wsUrl);
        } catch (e) {
            scheduleReconnect();
            return;
        }

        ws.onopen = () => {
            updateConnectionStatus("online");
            updateKioskUI();
            requestServerMiniTelemetry();
            startServerMiniTelemLoop();
            if (isTelemetryActive) {
                ws.send(JSON.stringify({ type: "REQUEST_TELEMETRY", target: telemetryTarget }));
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
            stopServerMiniTelemLoop();
            updateConnectionStatus("offline");
            scheduleReconnect();
        };

        ws.onerror = () => {
            stopServerMiniTelemLoop();
            try { ws.close(); } catch (_) {}
        };
    }

    function updateConnectionStatus(state) {
        if (!connStatus) return;
        if (state === "online") {
            connStatus.className = "brand-conn-line conn-online";
            if (connStatusText) connStatusText.textContent = "WS: ONLINE";
            connStatus.title = "Stato: Connesso al Master Server via WebSocket";
        } else if (state === "connecting") {
            connStatus.className = "brand-conn-line conn-connecting";
            if (connStatusText) connStatusText.textContent = "WS: CONNECTING";
            connStatus.title = "Stato: Connessione in corso al Master Server...";
        } else {
            stopServerMiniTelemLoop();
            connStatus.className = "brand-conn-line conn-offline";
            if (connStatusText) connStatusText.textContent = "WS: OFFLINE";
            connStatus.title = "Stato: Offline. Clicca per riconnettere subito";
            renderServerMiniTelemetryOffline();
            if (isTelemetryActive) {
                renderOfflineTelemetry(telemetryTarget);
            }
        }
    }

    function scheduleReconnect() {
        if (wsReconnectTimeout) clearTimeout(wsReconnectTimeout);
        wsReconnectTimeout = setTimeout(connectWebSocket, 2000);
    }

    if (connStatus) {
        connStatus.addEventListener("click", () => {
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
                if (msg.node_tag) updateBrandNodeTag(msg.node_tag);
                if (msg.node_tag === "KIOSK" || msg.is_kiosk) {
                    body.classList.remove("client-desktop", "client-mobile");
                    body.classList.add("client-kiosk");
                    document.documentElement.classList.remove("client-desktop", "client-mobile");
                    document.documentElement.classList.add("client-kiosk");
                } else if (msg.node_tag && msg.node_tag.startsWith("NODE-")) {
                    if (!/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "") && (window.innerWidth > 899 && window.innerHeight > 520)) {
                        body.classList.remove("client-kiosk", "client-mobile");
                        body.classList.add("client-desktop");
                        document.documentElement.classList.remove("client-kiosk", "client-mobile");
                        document.documentElement.classList.add("client-desktop");
                    }
                }
                if (msg.security_mode) updateSecurityMode(msg.security_mode);
                if (msg.state) setVisorState(msg.state);
                if (msg.is_muted !== undefined) updateMuteUI(msg.is_muted);
                if (msg.telemetry_active !== undefined) {
                    setTelemetryMode(msg.telemetry_active, msg.telemetry_target || "slave", false);
                }
                requestServerMiniTelemetry();
                resetInactivityTimer();
                break;

            case "STATE_CHANGE":
                // Handled inside setVisorState (wakes if active, or re-arms timer if IDLE)
                setVisorState(msg.state, msg.label, msg.sub);
                break;

            case "TELEMETRY_MODE":
                resetInactivityTimer();
                setTelemetryMode(msg.enabled, msg.target || "slave", false);
                if (msg.offline || msg.error || !msg.data) {
                    renderOfflineTelemetry(msg.target || "slave");
                } else if (msg.data) {
                    updateTelemetryData(msg.data, msg.target || "slave");
                }
                break;

            case "SERVER_TELEMETRY":
                if (msg.offline || msg.error || !msg.data) {
                    renderServerMiniTelemetryOffline();
                    if (isTelemetryActive && (telemetryTarget === "server" || telemetryTarget === "master")) {
                        renderOfflineTelemetry("server");
                    }
                } else if (msg.data) {
                    updateServerMiniTelemetry(msg.data);
                    if (isTelemetryActive && (telemetryTarget === "server" || telemetryTarget === "master")) {
                        updateTelemetryData(msg.data, "server");
                    }
                }
                break;

            case "TELEMETRY_UPDATE":
                // Passive background telemetry - do NOT reset inactivity timer!
                if (msg.offline || msg.error || !msg.data) {
                    const tgt = (msg.target || telemetryTarget || "slave").toLowerCase().trim();
                    if (tgt === "server" || tgt === "master") {
                        renderServerMiniTelemetryOffline();
                    }
                    renderOfflineTelemetry(tgt);
                } else if (msg.data) {
                    const tgt = (msg.target || telemetryTarget || "").toLowerCase().trim();
                    if (tgt === "server" || tgt === "master") {
                        updateServerMiniTelemetry(msg.data);
                    }
                    updateTelemetryData(msg.data, msg.target || telemetryTarget);
                }
                break;

            case "AUTH_EVENT":
                resetInactivityTimer();
                updateBiometrics(msg);
                break;

            case "TRANSCRIPT":
                resetInactivityTimer();
                if (msg.user_text && userTranscript) userTranscript.textContent = `"${msg.user_text}"`;
                if (msg.axl_text && axlReply) fitAxlReplyText(msg.axl_text);
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
    // 6. TELEMETRY ENGINE (Live Hardware Data + Clean Offline / N/A Fallback)
    // ==========================================================================

    // Helper to retrieve elements for the Primary Hardware Telemetry Panel
    function getPanelElements() {
        return {
            panel: telemetryPanel,
            title: telemTitle,
            badge: telemNodeBadge,
            cpuCardTitle: telemCpuCardTitle,
            cpuSub1Lbl: telemCpuSub1Lbl,
            cpuSub2Lbl: telemCpuSub2Lbl,
            cpuUsage: telemCpuUsage,
            cpuTemp: telemCpuTemp,
            cpuFreq: telemCpuFreq,
            cpuCores: telemCpuCores,
            cpuBar: telemCpuBar,
            ramCardTitle: telemRamCardTitle,
            ramUsed: telemRamUsed,
            ramTotal: telemRamTotal,
            ramPct: telemRamPct,
            ramFree: telemRamFree,
            ramBar: telemRamBar,
            gpuCardTitle: telemGpuCardTitle,
            gpuList: telemGpuList,
            gpuTemp: telemGpuGlobalTemp,
            fanRpm: telemFanRpm,
            fanUnit: telemFanUnit,
            fanStatus: telemFanStatus,
            fanLabel: telemFanLabel,
            fanSubval: telemFanSubval,
            fanCardTitle: telemFanCardTitle
        };
    }

    // Offline / fallback renderer for the Top Master Server Mini Sparklines
    function renderServerMiniTelemetryOffline() {
        updateMiniTelemVisibility();
        if (miniCpuFill) {
            miniCpuFill.style.width = "0%";
            miniCpuFill.className = "mini-fill muted";
        }
        if (miniCpuTemp) {
            miniCpuTemp.textContent = "N/A";
            miniCpuTemp.style.color = "var(--text-muted)";
        }
        if (miniGpuFill) {
            miniGpuFill.style.width = "0%";
            miniGpuFill.className = "mini-fill muted";
        }
        if (miniMemFill) {
            miniMemFill.style.width = "0%";
            miniMemFill.className = "mini-fill muted";
        }
    }

    // Offline / fallback renderer for the Primary Hardware Telemetry Panel
    function renderOfflineTelemetry(nodeName) {
        const els = getPanelElements();
        const tgt = (nodeName || telemetryTarget || "slave").toLowerCase().trim();
        const isKioskNode = (tgt === "kiosk");
        const isServerNode = (tgt === "server" || tgt === "master");

        if (els.panel) {
            els.panel.dataset.target = isKioskNode ? "kiosk" : tgt;
        }
        body.classList.toggle("telem-target-kiosk", isKioskNode);

        if (els.badge) {
            els.badge.className = "badge badge-telem-offline";
            if (isKioskNode) {
                els.badge.textContent = "NODE: KIOSK // OFFLINE";
            } else if (isServerNode) {
                els.badge.textContent = "NODE: MASTER // OFFLINE";
            } else {
                els.badge.textContent = `NODE: [${tgt.toUpperCase()}] // OFFLINE`;
            }
        }

        if (isKioskNode) {
            if (els.title) els.title.textContent = "KIOSK TELEMETRY";
            // 1. Processor (CPU)
            if (els.cpuCardTitle) els.cpuCardTitle.textContent = "PROCESSOR (CPU)";
            if (els.cpuUsage) els.cpuUsage.textContent = "N/A";
            if (els.cpuTemp) els.cpuTemp.textContent = "N/A";
            if (els.cpuSub1Lbl) els.cpuSub1Lbl.textContent = "SOC:";
            if (els.cpuFreq) els.cpuFreq.textContent = "Exynos 1280";
            if (els.cpuSub2Lbl) els.cpuSub2Lbl.textContent = "STATUS:";
            if (els.cpuCores) els.cpuCores.textContent = "OFFLINE";
            if (els.cpuBar) {
                els.cpuBar.style.width = "0%";
                els.cpuBar.className = "telem-meter-fill muted";
            }

            // 2. Memory (RAM)
            if (els.ramCardTitle) els.ramCardTitle.textContent = "MEMORY (RAM)";
            if (els.ramUsed) els.ramUsed.textContent = "N/A";
            if (els.ramTotal) els.ramTotal.textContent = "/ N/A";
            if (els.ramPct) els.ramPct.textContent = "N/A";
            if (els.ramFree) els.ramFree.textContent = "N/A";
            if (els.ramBar) {
                els.ramBar.style.width = "0%";
                els.ramBar.className = "telem-meter-fill muted";
            }

            // 3. Battery System
            if (els.gpuCardTitle) els.gpuCardTitle.textContent = "BATTERY SYSTEM";
            if (els.gpuTemp) els.gpuTemp.textContent = "N/A";
            if (els.gpuList) {
                els.gpuList.classList.remove("dual-gpu");
                els.gpuList.innerHTML = `
                    <div class="telem-gpu-item">
                        <div class="telem-gpu-top">
                            <span class="telem-gpu-name">Li-Ion Battery</span>
                            <span class="telem-gpu-badge offline">OFFLINE // N/A</span>
                        </div>
                        <div class="telem-meter-track">
                            <div class="telem-meter-fill muted" style="width: 0%;"></div>
                        </div>
                        <div class="telem-gpu-vram">STATUS: DISCONNECTED // OFFLINE</div>
                    </div>`;
            }

            // 4. Wi-Fi Link
            if (els.fanCardTitle) els.fanCardTitle.textContent = "WI-FI LINK";
            if (els.fanRpm) els.fanRpm.textContent = "N/A";
            if (els.fanUnit) els.fanUnit.textContent = "dBm";
            if (els.fanLabel) els.fanLabel.textContent = "SIGNAL RSSI";
            if (els.fanSubval) els.fanSubval.textContent = "OFFLINE";
            if (els.fanStatus) {
                els.fanStatus.textContent = "OFFLINE";
                els.fanStatus.className = "telem-temp-badge offline";
            }
            return;
        }

        // Standard PC / Server Node metrics
        if (els.title) {
            els.title.textContent = isServerNode ? "SERVER TELEMETRY" : "SLAVE TELEMETRY";
        }

        // 1. CPU
        if (els.cpuCardTitle) els.cpuCardTitle.textContent = "CPU CORE LOAD";
        if (els.cpuSub1Lbl) els.cpuSub1Lbl.textContent = "CLOCK:";
        if (els.cpuSub2Lbl) els.cpuSub2Lbl.textContent = "STATUS:";
        if (els.cpuUsage) els.cpuUsage.textContent = "N/A";
        if (els.cpuTemp) els.cpuTemp.textContent = "N/A";
        if (els.cpuFreq) els.cpuFreq.textContent = "-- MHz";
        if (els.cpuCores) els.cpuCores.textContent = "OFFLINE";
        if (els.cpuBar) {
            els.cpuBar.style.width = "0%";
            els.cpuBar.className = "telem-meter-fill muted";
        }

        // 2. RAM
        if (els.ramCardTitle) els.ramCardTitle.textContent = "MEMORY USAGE";
        if (els.ramUsed) els.ramUsed.textContent = "N/A";
        if (els.ramTotal) els.ramTotal.textContent = "/ N/A";
        if (els.ramPct) els.ramPct.textContent = "N/A";
        if (els.ramFree) els.ramFree.textContent = "N/A";
        if (els.ramBar) {
            els.ramBar.style.width = "0%";
            els.ramBar.className = "telem-meter-fill muted";
        }

        // 3. GPU(s)
        if (els.gpuCardTitle) els.gpuCardTitle.textContent = "GRAPHICS (GPU)";
        if (els.gpuTemp) els.gpuTemp.textContent = "N/A";
        if (els.gpuList) {
            els.gpuList.classList.remove("dual-gpu");
            els.gpuList.innerHTML = `
                <div class="telem-gpu-item">
                    <div class="telem-gpu-top">
                        <span class="telem-gpu-name">Graphics Device</span>
                        <span class="telem-gpu-badge offline">OFFLINE // N/A</span>
                    </div>
                    <div class="telem-meter-track">
                        <div class="telem-meter-fill muted" style="width: 0%;"></div>
                    </div>
                    <div class="telem-gpu-vram">STATUS: NO TELEMETRY FEED</div>
                </div>`;
        }

        // 4. Fans
        if (els.fanCardTitle) els.fanCardTitle.textContent = "COOLING FAN";
        if (els.fanUnit) els.fanUnit.textContent = "RPM";
        if (els.fanLabel) els.fanLabel.textContent = "SENSOR:";
        if (els.fanRpm) els.fanRpm.textContent = "N/A";
        if (els.fanSubval) els.fanSubval.textContent = "OFFLINE";
        if (els.fanStatus) {
            els.fanStatus.textContent = "OFFLINE";
            els.fanStatus.className = "telem-temp-badge offline";
        }
    }

    // Toggle visibility of the Top Master Server Mini Sparklines (hidden ONLY when full server telem is open)
    function updateMiniTelemVisibility() {
        if (!serverMiniTelem) return;
        const isFullServerActive = isTelemetryActive && (telemetryTarget === "server" || telemetryTarget === "master");
        serverMiniTelem.style.display = isFullServerActive ? "none" : "flex";
    }

    // Live update of the Top Master Server Mini Sparklines (CPU, MEM, GPU sliders + CPU Temp)
    function updateServerMiniTelemetry(data) {
        if (!data) return;
        updateMiniTelemVisibility();

        const cpu = data.cpu || {};
        const mem = data.memory || {};
        const gpus = data.gpus || [];

        // 1. CPU Load & Temperature
        const cpuUsage = Math.min(100, Math.max(0, Math.round(cpu.usage_percent || 0)));
        let cpuTemp = cpu.temperature_c;
        if (cpuTemp === undefined || cpuTemp === null) {
            const rawTemps = data.temperatures || {};
            for (const k of ["coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz"]) {
                if (rawTemps[k] && rawTemps[k].length > 0 && rawTemps[k][0].current) {
                    cpuTemp = Math.round(rawTemps[k][0].current);
                    break;
                }
            }
        }
        const resolvedTemp = (cpuTemp !== null && cpuTemp !== undefined) ? Math.round(cpuTemp) : null;

        if (miniCpuFill) {
            miniCpuFill.style.width = `${cpuUsage}%`;
            const isCrit = (cpuUsage >= 85) || (resolvedTemp !== null && resolvedTemp >= 82);
            const isWarn = !isCrit && (cpuUsage >= 70);
            miniCpuFill.className = "mini-fill" + (isCrit ? " crit" : (isWarn ? " warn" : ""));
        }
        if (miniCpuTemp) {
            miniCpuTemp.textContent = resolvedTemp !== null ? `${resolvedTemp}°C` : "--°C";
            miniCpuTemp.style.color = (resolvedTemp !== null && resolvedTemp >= 82) ? "var(--red-alert)" : "";
        }

        // 2. GPU Load (Slider only, no numbers)
        let gpuPct = 0;
        if (gpus.length > 0) {
            gpuPct = Math.round(Math.max(...gpus.map(g => g.usage_percent || 0)));
        } else {
            gpuPct = Math.round(cpuUsage * 0.45);
        }
        gpuPct = Math.min(100, Math.max(0, gpuPct));

        if (miniGpuFill) {
            miniGpuFill.style.width = `${gpuPct}%`;
            miniGpuFill.className = "mini-fill" + (gpuPct >= 85 ? " crit" : (gpuPct >= 70 ? " warn" : ""));
        }

        // 3. Memory Load (Slider only, no numbers)
        const memPct = Math.min(100, Math.max(0, Math.round(mem.percent || 0)));
        if (miniMemFill) {
            miniMemFill.style.width = `${memPct}%`;
            miniMemFill.className = "mini-fill" + (memPct >= 85 ? " crit" : (memPct >= 70 ? " warn" : ""));
        }
    }

    function formatGpuName(rawName, type) {
        if (!rawName || rawName === "N/A") return "N/A";
        let n = String(rawName).trim();
        n = n.replace(/^NVIDIA\s+GeForce\s+/i, "")
             .replace(/^NVIDIA\s+/i, "")
             .replace(/^AMD\s+Radeon\(TM\)\s+/i, "AMD ")
             .replace(/^AMD\s+Radeon\s+/i, "AMD ")
             .replace(/\s+Laptop\s+GPU$/i, "")
             .replace(/\s+with\s+Max-Q\s+Design$/i, " Max-Q")
             .replace(/\s+Graphics$/i, "")
             .trim();
        return n || (type === "dedicated" ? "dGPU" : "iGPU");
    }

    function renderNodeTelemetry(isSec, data, nodeName) {
        if (!data || data.offline) {
            renderOfflineTelemetry(nodeName);
            return;
        }
        const els = getPanelElements(isSec);
        const tgt = (nodeName || "slave").toLowerCase().trim();
        const isKioskNode = (tgt === "kiosk") || (data.battery_pct !== undefined);
        if (els.panel) {
            els.panel.dataset.target = isKioskNode ? "kiosk" : tgt;
        }
        body.classList.toggle("telem-target-kiosk", isKioskNode);

        if (isKioskNode) {
            // Render Android Kiosk metrics
            if (els.title) els.title.textContent = "KIOSK TELEMETRY";
            if (els.badge) {
                els.badge.className = "badge badge-telem-online";
                els.badge.textContent = "NODE: KIOSK // GALAXY A33";
            }
            const battTemp = data.battery_temp || 30.0;
            const battPct = (data.battery_pct !== undefined && data.battery_pct >= 0) ? data.battery_pct : 88;

            // 1. Card 1: PROCESSOR (CPU / SoC)
            if (els.cpuCardTitle) els.cpuCardTitle.textContent = "PROCESSOR (CPU)";
            const kioskCpu = (data.cpu_pct !== undefined) ? Math.round(data.cpu_pct) : Math.max(12, Math.min(65, Math.round(18 + (data.ram_used_pct ? data.ram_used_pct * 0.15 : 5))));
            if (els.cpuUsage) els.cpuUsage.textContent = `${kioskCpu}%`;
            if (els.cpuTemp) els.cpuTemp.textContent = `${battTemp.toFixed(1)}°C`;
            if (els.cpuSub1Lbl) els.cpuSub1Lbl.textContent = "SOC:";
            if (els.cpuFreq) els.cpuFreq.textContent = data.soc_name || "Exynos 1280";
            if (els.cpuSub2Lbl) els.cpuSub2Lbl.textContent = "CORES:";
            if (els.cpuCores) els.cpuCores.textContent = `${data.cpu_cores || 8} Cores`;
            if (els.cpuBar) {
                els.cpuBar.style.width = `${kioskCpu}%`;
                els.cpuBar.className = kioskCpu >= 85 ? "telem-meter-fill crit" : (kioskCpu >= 70 ? "telem-meter-fill warn" : "telem-meter-fill");
            }

            // 2. Card 2: MEMORY (RAM)
            if (els.ramCardTitle) els.ramCardTitle.textContent = "MEMORY (RAM)";
            const ramUsedPct = Math.round(data.ram_used_pct || 48);
            const ramFreeGb = data.ram_free_mb ? (data.ram_free_mb / 1024).toFixed(1) : "2.6";
            const ramTotalGb = 6.0;
            const ramUsedGb = (ramTotalGb - parseFloat(ramFreeGb)).toFixed(1);
            if (els.ramUsed) els.ramUsed.textContent = ramUsedGb;
            if (els.ramTotal) els.ramTotal.textContent = `/ ${ramTotalGb.toFixed(1)} GB`;
            if (els.ramPct) els.ramPct.textContent = `${ramUsedPct}%`;
            if (els.ramFree) els.ramFree.textContent = `${ramFreeGb} GB`;
            if (els.ramBar) {
                els.ramBar.style.width = `${ramUsedPct}%`;
                els.ramBar.className = ramUsedPct >= 85 ? "telem-meter-fill crit" : (ramUsedPct >= 70 ? "telem-meter-fill warn" : "telem-meter-fill");
            }

            // 3. Card 3: BATTERY SYSTEM (replaces GPU)
            if (els.gpuCardTitle) els.gpuCardTitle.textContent = "BATTERY SYSTEM";
            if (els.gpuList) {
                const chargeLabel = data.is_charging ? "⚡ AC CHARGING" : "🔋 DISCHARGING";
                const battColorClass = battPct <= 20 ? "crit" : (battPct <= 35 ? "warn" : "");
                els.gpuList.classList.remove("dual-gpu");
                els.gpuList.innerHTML = `
                    <div class="telem-gpu-item">
                        <div class="telem-gpu-top">
                            <span class="telem-gpu-name">Li-Ion 5000 mAh</span>
                            <span class="telem-gpu-badge">${chargeLabel} // ${battPct}%</span>
                        </div>
                        <div class="telem-meter-track">
                            <div class="telem-meter-fill ${battColorClass}" style="width: ${battPct}%;"></div>
                        </div>
                        <div class="telem-gpu-vram">STATUS: ${data.is_charging ? "CHARGING // FAST" : "ON BATTERY // NORMAL"}</div>
                    </div>`;
                if (els.gpuTemp) els.gpuTemp.textContent = `${battTemp.toFixed(1)}°C`;
            }

            // 4. Card 4: WI-FI LINK (replaces COOLING FAN)
            const rssi = (data.wifi_rssi_dbm !== undefined) ? data.wifi_rssi_dbm : -58;
            if (els.fanCardTitle) els.fanCardTitle.textContent = "WI-FI LINK";
            if (els.fanRpm) els.fanRpm.textContent = `${rssi}`;
            if (els.fanUnit) els.fanUnit.textContent = "dBm";
            if (els.fanLabel) els.fanLabel.textContent = "SIGNAL RSSI";
            if (els.fanSubval) els.fanSubval.textContent = rssi >= -65 ? "EXCELLENT" : (rssi >= -80 ? "GOOD" : "WEAK");
            if (els.fanStatus) {
                els.fanStatus.textContent = rssi >= -65 ? "ONLINE" : (rssi >= -80 ? "ACTIVE" : "FAIR");
                els.fanStatus.className = "telem-temp-badge";
            }
            return;
        }

        // Standard PC / Server Node metrics
        const isServerNode = (tgt === "server" || tgt === "master");
        if (els.title) {
            els.title.textContent = isServerNode ? "SERVER TELEMETRY" : "SLAVE TELEMETRY";
        }
        if (els.badge) {
            els.badge.className = "badge badge-telem-online";
            els.badge.textContent = isServerNode ? "NODE: MASTER // LIVE" : `NODE: [${tgt.toUpperCase()}] // LIVE`;
        }

        // Restore standard PC / Server Node card titles and labels
        if (els.cpuCardTitle) els.cpuCardTitle.textContent = "CPU CORE LOAD";
        if (els.cpuSub1Lbl) els.cpuSub1Lbl.textContent = "CLOCK:";
        if (els.cpuSub2Lbl) els.cpuSub2Lbl.textContent = "CORES:";
        if (els.ramCardTitle) els.ramCardTitle.textContent = "MEMORY USAGE";
        if (els.gpuCardTitle) els.gpuCardTitle.textContent = "GRAPHICS (GPU)";
        if (els.fanCardTitle) els.fanCardTitle.textContent = "COOLING FAN";
        if (els.fanUnit) els.fanUnit.textContent = "RPM";
        if (els.fanLabel) els.fanLabel.textContent = "SENSOR:";

        // 1. CPU
        const cpu = data.cpu || {};
        const cpuUsage = Math.round(cpu.usage_percent || 0);
        if (els.cpuUsage) els.cpuUsage.textContent = `${cpuUsage}%`;
        if (els.cpuBar) {
            els.cpuBar.style.width = `${Math.min(100, Math.max(0, cpuUsage))}%`;
            if (cpuUsage >= 85) els.cpuBar.className = "telem-meter-fill crit";
            else if (cpuUsage >= 70) els.cpuBar.className = "telem-meter-fill warn";
            else els.cpuBar.className = "telem-meter-fill";
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
        if (els.cpuTemp) {
            els.cpuTemp.textContent = (resolvedCpuTemp !== null && resolvedCpuTemp !== undefined)
                ? `${Math.round(resolvedCpuTemp)}°C` : "44°C";
        }
        if (els.cpuFreq) {
            els.cpuFreq.textContent = cpu.freq_mhz ? `${Math.round(cpu.freq_mhz)} MHz` : "-- MHz";
        }
        if (els.cpuCores) {
            els.cpuCores.textContent = `${cpu.core_count || 4} Cores`;
        }

        // 2. RAM
        const mem = data.memory || {};
        const ramUsed = (mem.used_gb || 0).toFixed(1);
        const ramTot = (mem.total_gb || 0).toFixed(1);
        const ramPct = Math.round(mem.percent || 0);
        const ramFree = (mem.available_gb || 0).toFixed(1);

        if (els.ramUsed) els.ramUsed.textContent = ramUsed;
        if (els.ramTotal) els.ramTotal.textContent = `/ ${ramTot} GB`;
        if (els.ramPct) els.ramPct.textContent = `${ramPct}%`;
        if (els.ramFree) els.ramFree.textContent = `${ramFree} GB`;
        if (els.ramBar) {
            els.ramBar.style.width = `${Math.min(100, Math.max(0, ramPct))}%`;
            if (ramPct >= 85) els.ramBar.className = "telem-meter-fill crit";
            else if (ramPct >= 70) els.ramBar.className = "telem-meter-fill warn";
            else els.ramBar.className = "telem-meter-fill";
        }

        // 3. GPU(s)
        const gpus = data.gpus || [];
        if (els.gpuList) {
            if (gpus.length === 0) {
                const fallbackLoad = Math.max(8, Math.round(cpuUsage * 0.45));
                els.gpuList.innerHTML = `
                    <div class="telem-gpu-item">
                        <div class="telem-gpu-top">
                            <span class="telem-gpu-name">Integrated Graphics</span>
                            <span class="telem-gpu-badge">INTEGRATED // ${fallbackLoad}%</span>
                        </div>
                        <div class="telem-meter-track">
                            <div class="telem-meter-fill" style="width: ${fallbackLoad}%;"></div>
                        </div>
                    </div>`;
                if (els.gpuTemp) {
                    els.gpuTemp.textContent = (resolvedCpuTemp !== null && resolvedCpuTemp !== undefined)
                        ? `${Math.round(resolvedCpuTemp)}°C` : "44°C";
                }
            } else {
                let html = "";
                let maxTemp = null;
                const isDesktopClient = document.documentElement.classList.contains("client-desktop") || 
                                        body.classList.contains("client-desktop") || 
                                        (!clientEnv.isMobile && window.innerWidth > 899 && window.innerHeight > 520);
                const isDualGpu = gpus.length >= 2;
                const useCompactDualLayout = !isDesktopClient && isDualGpu;

                gpus.forEach((gpu) => {
                    const isDed = gpu.type === "dedicated";
                    const gUsage = Math.round(gpu.usage_percent || 0);
                    const tempStr = (gpu.temperature_c !== null && gpu.temperature_c !== undefined)
                        ? `${Math.round(gpu.temperature_c)}°C`
                        : (gpu.freq_mhz ? `${Math.round(gpu.freq_mhz)} MHz` : `${gUsage}%`);

                    if (gpu.temperature_c !== null && gpu.temperature_c !== undefined) {
                        if (maxTemp === null || gpu.temperature_c > maxTemp) maxTemp = gpu.temperature_c;
                    }

                    if (useCompactDualLayout) {
                        // Mobile Landscape / compact dual-GPU micro-layout (2 side-by-side columns)
                        const shortName = formatGpuName(gpu.name, gpu.type);
                        const typeTag = isDed ? "dGPU" : "iGPU";
                        let vramInfo = "";
                        if (gpu.memory_used_mb && gpu.memory_total_mb) {
                            const uGb = (gpu.memory_used_mb / 1024).toFixed(1);
                            const tGb = (gpu.memory_total_mb / 1024).toFixed(1);
                            vramInfo = `<span class="telem-gpu-vram-compact">${uGb}/${tGb} GB</span>`;
                        } else if (!isDed) {
                            vramInfo = gpu.freq_mhz ? `<span class="telem-gpu-vram-compact">${Math.round(gpu.freq_mhz)} MHz</span>` : "";
                        }

                        html += `
                        <div class="telem-gpu-item">
                            <div class="telem-gpu-top">
                                <span class="telem-gpu-name" title="${escapeHtml(gpu.name || 'N/A')}">${escapeHtml(shortName)}</span>
                                <span class="telem-gpu-badge">${typeTag} // ${tempStr}</span>
                            </div>
                            <div class="telem-meter-track">
                                <div class="telem-meter-fill" style="width: ${Math.min(100, Math.max(0, gUsage))}%;"></div>
                            </div>
                            <div class="telem-gpu-sub-row">
                                <span class="sub-lbl">LOAD: ${gUsage}%</span>
                                ${vramInfo}
                            </div>
                        </div>`;
                    } else {
                        // Desktop PC / Tablet / Single GPU: Full-size, readable stacked layout
                        const fullName = gpu.name || 'N/A';
                        const typeTag = isDed ? "DEDICATED" : "INTEGRATED";
                        let vramInfo = "";
                        if (gpu.memory_used_mb && gpu.memory_total_mb) {
                            const uGb = (gpu.memory_used_mb / 1024).toFixed(1);
                            const tGb = (gpu.memory_total_mb / 1024).toFixed(1);
                            vramInfo = `<div class="telem-gpu-vram">VRAM: ${uGb} / ${tGb} GB</div>`;
                        } else if (gpu.freq_mhz) {
                            vramInfo = `<div class="telem-gpu-vram">FREQ: ${Math.round(gpu.freq_mhz)} MHz</div>`;
                        }

                        html += `
                        <div class="telem-gpu-item">
                            <div class="telem-gpu-top">
                                <span class="telem-gpu-name" title="${escapeHtml(fullName)}">${escapeHtml(fullName)}</span>
                                <span class="telem-gpu-badge">${typeTag} // ${tempStr}</span>
                            </div>
                            <div class="telem-meter-track">
                                <div class="telem-meter-fill" style="width: ${Math.min(100, Math.max(0, gUsage))}%;"></div>
                            </div>
                            ${vramInfo}
                        </div>`;
                    }
                });
                els.gpuList.classList.toggle("dual-gpu", useCompactDualLayout);
                els.gpuList.innerHTML = html;
                if (els.gpuTemp) {
                    els.gpuTemp.textContent = maxTemp !== null ? `${Math.round(maxTemp)}°C` : "45°C";
                }
            }
        }

        // 4. Fans
        if (els.fanCardTitle) els.fanCardTitle.textContent = "COOLING FAN";
        if (els.fanUnit) els.fanUnit.textContent = "RPM";
        if (els.fanLabel) els.fanLabel.textContent = "SENSOR:";
        const fans = data.fans || [];
        if (fans.length > 0) {
            const f = fans[0];
            if (els.fanRpm) els.fanRpm.textContent = f.rpm;
            if (els.fanSubval) els.fanSubval.textContent = (f.label || "CPU FAN").toUpperCase();
            if (els.fanStatus) {
                els.fanStatus.textContent = f.rpm > 0 ? "ACTIVE" : "IDLE";
                els.fanStatus.className = "telem-temp-badge";
            }
        } else {
            const estRpm = Math.round(1450 + cpuUsage * 8);
            if (els.fanRpm) els.fanRpm.textContent = estRpm;
            if (els.fanSubval) els.fanSubval.textContent = "SYSTEM COOLING";
            if (els.fanStatus) {
                els.fanStatus.textContent = "AUTO";
                els.fanStatus.className = "telem-temp-badge";
            }
        }
    }

    function startTelemetryLoop() {
        stopTelemetryLoop();
        pollTelemetryCycle();
        telemetrySimInterval = setInterval(() => {
            if (!isTelemetryActive) {
                stopTelemetryLoop();
                return;
            }
            pollTelemetryCycle();
        }, 1500);
    }

    function stopTelemetryLoop() {
        if (telemetrySimInterval) {
            clearInterval(telemetrySimInterval);
            telemetrySimInterval = null;
        }
    }

    function pollTelemetryCycle() {
        if (!isTelemetryActive) return;
        const tgt = (telemetryTarget || "slave").toLowerCase().trim();
        if (tgt === "kiosk") {
            fetchKioskTelemetry(false);
        } else {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "REQUEST_TELEMETRY", target: tgt }));
            } else {
                renderOfflineTelemetry(tgt);
            }
            if (Date.now() - lastRealTelemetryTime > 4000) {
                renderOfflineTelemetry(tgt);
            }
        }
    }

    function fetchKioskTelemetry(isSec = false) {
        if (window.AXLKiosk && typeof window.AXLKiosk.getKioskTelemetry === "function") {
            try {
                const raw = window.AXLKiosk.getKioskTelemetry();
                if (raw) {
                    const data = typeof raw === "string" ? JSON.parse(raw) : raw;
                    lastRealTelemetryTime = Date.now();
                    renderNodeTelemetry(false, data, "kiosk");
                    return;
                }
            } catch (err) {
                console.warn("[KIOSK] Telemetry bridge error:", err);
            }
        }
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "REQUEST_TELEMETRY",
                target: "kiosk"
            }));
        } else {
            renderOfflineTelemetry("kiosk");
        }
        if (Date.now() - lastRealTelemetryTime > 4000) {
            renderOfflineTelemetry("kiosk");
        }
    }

    function setTelemetryMode(enabled, target, notifyServer = true) {
        isTelemetryActive = !!enabled;
        if (target) {
            telemetryTarget = target.toLowerCase().trim();
        }

        body.classList.toggle("telemetry-active", isTelemetryActive);
        body.classList.remove("telem-target-kiosk", "telem-target-slave", "telem-target-server");
        if (isTelemetryActive) {
            body.classList.add(`telem-target-${telemetryTarget}`);
        }

        if (telemetryPanel) {
            telemetryPanel.hidden = !isTelemetryActive;
            telemetryPanel.style.display = isTelemetryActive ? "flex" : "none";
            if (isTelemetryActive) {
                telemetryPanel.dataset.target = telemetryTarget;
            } else {
                delete telemetryPanel.dataset.target;
            }
        }

        // Update dropdown button label and state
        if (telemBtn) {
            telemBtn.classList.toggle("active", isTelemetryActive);
            if (!isTelemetryActive) {
                telemBtn.textContent = "TELEM: OFF ▾";
            } else {
                let tgtLabel = telemetryTarget.toUpperCase();
                if (telemetryTarget === "server" || telemetryTarget === "master") tgtLabel = "SERVER";
                else if (telemetryTarget === "slave") tgtLabel = "SLAVE";
                else if (telemetryTarget === "kiosk") tgtLabel = "KIOSK";
                telemBtn.textContent = `TELEM: ${tgtLabel} ▾`;
            }
        }

        // Update dropdown active highlight
        [telemOptServer, telemOptSlave, telemOptKiosk].forEach(btn => {
            if (btn) btn.classList.remove("active");
        });

        if (isTelemetryActive) {
            lastRealTelemetryTime = Date.now();
            if (telemetryTarget === "server" || telemetryTarget === "master") {
                if (telemOptServer) telemOptServer.classList.add("active");
            } else if (telemetryTarget === "kiosk") {
                if (telemOptKiosk) telemOptKiosk.classList.add("active");
            } else {
                if (telemOptSlave) telemOptSlave.classList.add("active");
            }
            startTelemetryLoop();
        } else {
            stopTelemetryLoop();
        }

        updateMiniTelemVisibility();

        if (notifyServer && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "TOGGLE_TELEMETRY",
                enabled: isTelemetryActive,
                target: telemetryTarget
            }));
        }

        // Smoothly resize visor canvas as layout transitions
        setTimeout(resizeCanvas, 30);
        setTimeout(resizeCanvas, 200);
        setTimeout(resizeCanvas, 400);
    }

    function updateTelemetryData(data, target) {
        const tgt = (target || telemetryTarget || "slave").toLowerCase().trim();
        if (!data || data.offline) {
            renderOfflineTelemetry(tgt);
            return;
        }
        lastRealTelemetryTime = Date.now();
        renderNodeTelemetry(false, data, tgt);
    }

    // Unified Telemetry Dropdown Controller
    function closeTelemPopup() {
        if (telemPopup) {
            telemPopup.classList.remove("open");
            telemPopup.hidden = true;
        }
        if (telemBtn) telemBtn.classList.remove("open");
    }

    function toggleTelemPopup() {
        if (!telemPopup) return;
        const isOpen = telemPopup.classList.contains("open");
        if (isOpen) {
            closeTelemPopup();
        } else {
            closeSettingsPopup();
            closeModePopup();
            updateKioskUI();
            telemPopup.hidden = false;
            telemPopup.classList.add("open");
            if (telemBtn) telemBtn.classList.add("open");
        }
    }

    if (telemBtn) {
        telemBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleTelemPopup();
        });
    }

    if (telemOptServer) {
        telemOptServer.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeTelemPopup();
            setTelemetryMode(true, "server", true);
        });
    }

    if (telemOptSlave) {
        telemOptSlave.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeTelemPopup();
            setTelemetryMode(true, "slave", true);
        });
    }

    if (telemOptKiosk) {
        telemOptKiosk.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (telemOptKiosk.disabled) return;
            closeTelemPopup();
            setTelemetryMode(true, "kiosk", true);
        });
    }

    if (telemOptClose) {
        telemOptClose.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            closeTelemPopup();
            setTelemetryMode(false, telemetryTarget, true);
        });
    }

    if (telemCloseBtn) {
        telemCloseBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            setTelemetryMode(false, telemetryTarget, true);
        });
    }

    document.addEventListener("click", (e) => {
        if (telemPopup && telemPopup.classList.contains("open")) {
            if (!telemPopup.contains(e.target) && e.target !== telemBtn) {
                closeTelemPopup();
            }
        }
    });

    // ==========================================================================
    // 7. ONE-WAY SECURITY MODE CONTROLLER (Clickable ONLY when on FRIENDLY)
    // ==========================================================================
    function closeModePopup() {
        if (modeSelectorPopup) {
            modeSelectorPopup.classList.remove("open");
            modeSelectorPopup.hidden = true;
        }
        if (secModeBadge) {
            secModeBadge.classList.remove("open");
        }
    }

    function updateAskButton() {
        if (!askBtn) return;
        askBtn.classList.remove("ask-friendly", "ask-restrict", "ask-paranoid", "ask-stop");

        // When A-X-L is speaking, dynamically transform ASK button into an interactive red STOP button!
        if (currentState === "SPEAKING") {
            askBtn.disabled = false;
            askBtn.classList.remove("disabled-muted", "listening");
            askBtn.classList.add("ask-stop");
            if (askBtnIcon) askBtnIcon.textContent = "⏹️";
            if (askBtnLabel) askBtnLabel.textContent = "STOP";
            askBtn.title = "Interrompi la risposta vocale di A-X-L";
            return;
        }

        askBtn.classList.add(`ask-${securityMode}`);
        const listening = currentState === "LISTENING";
        askBtn.classList.toggle("listening", listening);

        // When microphone is muted, ASK is strictly disabled and unclickable
        if (isMuted) {
            askBtn.disabled = true;
            askBtn.classList.add("disabled-muted");
            if (askBtnIcon) askBtnIcon.textContent = "🔇";
            if (askBtnLabel) askBtnLabel.textContent = "MIC MUTO";
            askBtn.title = "Microfono disattivato: sblocca prima il pulsante MUTE per abilitare le richieste vocali.";
        } else {
            askBtn.disabled = currentState !== "IDLE";
            askBtn.classList.remove("disabled-muted");
            if (askBtnIcon) askBtnIcon.textContent = listening ? "🎙️" : "💬";
            if (askBtnLabel) askBtnLabel.textContent = listening ? "LISTENING..." : "ASK ME...";
            askBtn.title = listening
                ? "Ascolto in corso..."
                : (securityMode === "friendly"
                    ? "Fai una domanda senza dire la wake-word"
                    : "Fai una domanda senza wake-word (verifica vocale ancora attiva)");
        }
    }

    if (askBtn) {
        askBtn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();

            // If clicked while A-X-L is speaking, immediately abort vocal delivery!
            if (currentState === "SPEAKING") {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "STOP_SPEECH" }));
                }
                setVisorState("IDLE");
                return;
            }

            if (askBtn.disabled || isMuted) return;
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

    function syncModePopupUI() {
        const modeOptFriendly = document.getElementById("mode-opt-friendly");
        const modeOptRestrict = document.getElementById("mode-opt-restrict");
        const modeOptParanoid = document.getElementById("mode-opt-paranoid");
        const modeLockMsg = document.getElementById("mode-popup-lock-msg");
        const modeHeader = document.getElementById("mode-popup-header");

        const isFriendly = (securityMode === "friendly");

        if (modeHeader) {
            modeHeader.textContent = isFriendly ? "IMPOSTA SICUREZZA (BLOCCO VOCALE):" : "STATO SICUREZZA (BLOCCO ATTIVO):";
        }

        if (modeLockMsg) {
            modeLockMsg.style.display = isFriendly ? "none" : "block";
            modeLockMsg.textContent = `🔒 Sicurezza bloccata su ${securityMode.toUpperCase()}. Solo l'Owner a voce può sbloccare.`;
        }

        const btnMap = [
            { btn: modeOptFriendly, mode: "friendly" },
            { btn: modeOptRestrict, mode: "restrict" },
            { btn: modeOptParanoid, mode: "paranoid" }
        ];

        btnMap.forEach(({ btn, mode }) => {
            if (!btn) return;
            const isCurrent = (securityMode === mode);
            btn.classList.toggle("active-mode", isCurrent);

            if (isFriendly) {
                // In friendly mode, all 3 options are usable and colored
                btn.disabled = false;
                btn.classList.remove("mode-btn-locked", "active-locked");
                if (isCurrent) {
                    btn.title = "Modalità corrente: FRIENDLY (Accesso Libero)";
                } else {
                    btn.title = `Clicca per impostare e bloccare su ${mode.toUpperCase()}`;
                }
            } else {
                // In restrict or paranoid mode, all options are grayed out (disabled/locked)
                btn.disabled = true;
                btn.classList.add("mode-btn-locked");
                btn.classList.toggle("active-locked", isCurrent);
                if (isCurrent) {
                    btn.title = `Modalità ATTIVA: ${mode.toUpperCase()} (Bloccata da sicurezza biometrica)`;
                } else {
                    btn.title = "Opzione bloccata: modifica consentita solo all'Owner a voce";
                }
            }
        });
    }

    function updateSecurityMode(mode) {
        securityMode = (mode || "friendly").toLowerCase().trim();
        closeModePopup();
        updateAskButton();
        syncModePopupUI();

        if (!secModeBadge) return;

        if (securityMode === "friendly") {
            secModeBadge.className = "badge badge-friendly mode-unlocked";
            secModeBadge.textContent = "MODE: FRIENDLY ▾";
            secModeBadge.title = "Clicca per visualizzare o bloccare la sicurezza su Restrict o Paranoid";
        } else {
            secModeBadge.className = `badge badge-${securityMode} mode-locked`;
            secModeBadge.textContent = `🔒 MODE: ${securityMode.toUpperCase()} ▾`;
            secModeBadge.title = `Sicurezza ${securityMode.toUpperCase()} bloccata. Clicca per visualizzare lo stato. Sblocco possibile solo dall'Owner a voce.`;
        }
    }

    if (secModeBadge) {
        secModeBadge.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();

            if (modeSelectorPopup) {
                const isOpen = modeSelectorPopup.classList.contains("open");
                if (isOpen) {
                    closeModePopup();
                } else {
                    closeSettingsPopup();
                    closeTelemPopup();
                    syncModePopupUI();
                    modeSelectorPopup.hidden = false;
                    modeSelectorPopup.classList.add("open");
                    secModeBadge.classList.add("open");

                    if (securityMode !== "friendly" && axlReply) {
                        fitAxlReplyText(`Sicurezza bloccata su ${securityMode.toUpperCase()}. Solo l'Owner con verifica vocale può sbloccare il sistema.`);
                    }
                }
            }
        });
    }

    document.querySelectorAll(".mode-option-btn[data-mode]").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();

            if (securityMode !== "friendly") {
                if (axlReply) {
                    fitAxlReplyText(`Azione rifiutata: sicurezza bloccata su ${securityMode.toUpperCase()}. Solo l'Owner tramite comando vocale può sbloccare.`);
                }
                return;
            }

            const targetMode = btn.getAttribute("data-mode");
            if (targetMode === "restrict" || targetMode === "paranoid") {
                updateSecurityMode(targetMode);
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: "SET_SECURITY_MODE",
                        mode: targetMode
                    }));
                }
                if (axlReply) {
                    fitAxlReplyText(`Modalità ${targetMode.toUpperCase()} attivata e bloccata. Verifica biometrica vocale attiva.`);
                }
            } else if (targetMode === "friendly") {
                closeModePopup();
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
                fitAxlReplyText("Modalità muto attiva. Sensori audio disattivati (sblocco consentito solo tramite questo pulsante).");
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

        if (bioIdentity) {
            bioIdentity.textContent = id;
            bioIdentity.classList.remove("bio-id-admin", "bio-id-owner", "bio-id-stranger");
            if (id === "ADMIN_TERMINAL") {
                bioIdentity.classList.add("bio-id-admin");
            } else if (id === "OWNER") {
                bioIdentity.classList.add("bio-id-owner");
            } else if (!auth) {
                bioIdentity.classList.add("bio-id-stranger");
            }
        }
        if (bioScoreText) bioScoreText.textContent = score.toFixed(3);
        const percent = Math.min(100, Math.max(0, Math.round(score * 100)));
        if (bioScoreBar) bioScoreBar.style.width = `${percent}%`;

        if (bioAuthStatus) {
            bioAuthStatus.className = "bio-value";
            if (id === "ADMIN_TERMINAL") {
                bioAuthStatus.classList.add("auth-owner");
                bioAuthStatus.textContent = "ADMIN / AUTH";
            } else if (id === "OWNER") {
                bioAuthStatus.classList.add("auth-owner");
                bioAuthStatus.textContent = "ADMIN";
            } else if (auth) {
                bioAuthStatus.classList.add("auth-friend");
                bioAuthStatus.textContent = "AUTHORIZED";
            } else {
                bioAuthStatus.classList.add("auth-stranger");
                bioAuthStatus.textContent = "STRANGER";
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
    updateAskButton();
    syncModePopupUI();
    updateMiniTelemVisibility();

})();
