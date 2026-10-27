/**
 * A-X-L CYBERNETIC VISOR // HUD CLIENT ENGINE
 * Handles:
 * 1. WebSocket link to Master Server (/ws/ui)
 * 2. 60FPS Canvas Visor Animation (Idle, Listening, Thinking, Speaking, Alert)
 * 3. Biometric telemetry & Live Subtitles
 * 4. OLED Pixel-Shifting anti-burn-in protection (Samsung Galaxy A33)
 */

(function () {
    "use strict";

    // --- DOM Elements ---
    const body = document.body;
    const canvas = document.getElementById("visor-canvas");
    const ctx = canvas.getContext("2d");
    const clockTime = document.getElementById("clock-time");
    const clockDate = document.getElementById("clock-date");
    const secModeBadge = document.getElementById("sec-mode-badge");
    const connBadge = document.getElementById("connection-badge");
    const stateLabel = document.getElementById("visor-state-label");
    const stateSub = document.getElementById("visor-sub-label");
    const bioIdentity = document.getElementById("bio-identity");
    const bioScoreBar = document.getElementById("bio-score-bar");
    const bioScoreText = document.getElementById("bio-score-text");
    const bioAuthStatus = document.getElementById("bio-auth-status");
    const userTranscript = document.getElementById("user-transcript");
    const axlReply = document.getElementById("axl-reply");
    const fullscreenBtn = document.getElementById("fullscreen-btn");

    // --- State Variables ---
    let currentState = "IDLE";
    let securityMode = "friendly";
    let ws = null;
    let wsReconnectTimeout = null;
    let animFrameId = null;
    let animTime = 0;
    let idleInactivityTimer = null;

    // Canvas resolution setup
    function resizeCanvas() {
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * window.devicePixelRatio;
        canvas.height = rect.height * window.devicePixelRatio;
        ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    }
    window.addEventListener("resize", resizeCanvas);
    resizeCanvas();

    // ==========================================================================
    // 1. OLED PIXEL-SHIFTING ANTI-BURN-IN SYSTEM (GALAXY A33)
    // ==========================================================================
    const PIXEL_SHIFT_INTERVAL_MS = 60000; // Shift every 60 seconds
    const SUBDUED_INACTIVITY_MS = 90000;   // Dim after 90s idle

    function triggerPixelShift() {
        // Random offset bounded within [-8px, +8px]
        const shiftX = (Math.floor(Math.random() * 17) - 8);
        const shiftY = (Math.floor(Math.random() * 17) - 8);
        document.documentElement.style.setProperty("--hud-shift-x", `${shiftX}px`);
        document.documentElement.style.setProperty("--hud-shift-y", `${shiftY}px`);
    }
    setInterval(triggerPixelShift, PIXEL_SHIFT_INTERVAL_MS);

    function resetInactivityTimer() {
        body.classList.remove("hud-subdued");
        if (idleInactivityTimer) clearTimeout(idleInactivityTimer);
        if (currentState === "IDLE") {
            idleInactivityTimer = setTimeout(() => {
                body.classList.add("hud-subdued");
            }, SUBDUED_INACTIVITY_MS);
        }
    }
    window.addEventListener("pointerdown", resetInactivityTimer);
    window.addEventListener("keydown", resetInactivityTimer);

    // ==========================================================================
    // 2. TACTICAL CLOCK & DATE
    // ==========================================================================
    function updateClock() {
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, "0");
        const mm = String(now.getMinutes()).padStart(2, "0");
        const ss = String(now.getSeconds()).padStart(2, "0");
        clockTime.textContent = `${hh}:${mm}:${ss}`;

        const options = { weekday: "short", day: "2-digit", month: "short", year: "numeric" };
        clockDate.textContent = now.toLocaleDateString("it-IT", options).toUpperCase();
    }
    setInterval(updateClock, 500);
    updateClock();

    // ==========================================================================
    // 3. FULLSCREEN CONTROLLER
    // ==========================================================================
    fullscreenBtn.addEventListener("click", () => {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(() => {});
        } else {
            document.exitFullscreen().catch(() => {});
        }
    });

    // ==========================================================================
    // 4. WEBSOCKET HUD CLIENT
    // ==========================================================================
    function connectWebSocket() {
        if (wsReconnectTimeout) clearTimeout(wsReconnectTimeout);

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws/ui`;

        connBadge.className = "badge badge-offline";
        connBadge.textContent = "WS: CONNECTING";

        try {
            ws = new WebSocket(wsUrl);
        } catch (e) {
            scheduleReconnect();
            return;
        }

        ws.onopen = () => {
            connBadge.className = "badge badge-online";
            connBadge.textContent = "WS: ONLINE";
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
            connBadge.className = "badge badge-offline";
            connBadge.textContent = "WS: OFFLINE";
            scheduleReconnect();
        };

        ws.onerror = () => {
            ws.close();
        };
    }

    function scheduleReconnect() {
        if (wsReconnectTimeout) clearTimeout(wsReconnectTimeout);
        wsReconnectTimeout = setTimeout(connectWebSocket, 2000);
    }

    // ==========================================================================
    // 5. SERVER MESSAGE DISPATCHER
    // ==========================================================================
    function handleServerMessage(msg) {
        resetInactivityTimer();

        switch (msg.type) {
            case "INIT":
                if (msg.security_mode) updateSecurityMode(msg.security_mode);
                if (msg.state) setVisorState(msg.state);
                break;

            case "STATE_CHANGE":
                setVisorState(msg.state, msg.label, msg.sub);
                break;

            case "AUTH_EVENT":
                updateBiometrics(msg);
                break;

            case "TRANSCRIPT":
                if (msg.user_text) userTranscript.textContent = `"${msg.user_text}"`;
                if (msg.axl_text) axlReply.textContent = msg.axl_text;
                break;

            case "SECURITY_MODE":
                updateSecurityMode(msg.mode);
                break;

            case "ANIMATION":
                if (msg.name) switch3DAnimation(msg.name);
                if (msg.camera) setCameraMode(msg.camera);
                break;

            default:
                break;
        }
    }

    function updateSecurityMode(mode) {
        securityMode = (mode || "friendly").toLowerCase();
        secModeBadge.className = `badge badge-${securityMode}`;
        secModeBadge.textContent = `MODE: ${securityMode.toUpperCase()}`;
    }

    function updateBiometrics(data) {
        const id = (data.identity || "UNKNOWN").toUpperCase();
        const score = typeof data.score === "number" ? data.score : 0.0;
        const auth = !!data.authorized;

        bioIdentity.textContent = id;
        bioScoreText.textContent = score.toFixed(3);
        const percent = Math.min(100, Math.max(0, Math.round(score * 100)));
        bioScoreBar.style.width = `${percent}%`;

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

    function setVisorState(state, customLabel, customSub) {
        currentState = (state || "IDLE").toUpperCase();
        body.className = `state-${currentState.toLowerCase()}`;

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
        stateLabel.textContent = customLabel || config.label;
        stateSub.textContent = customSub || config.sub;

        // Synchronize 3D Holographic Wireframe Dog
        if (wireframeMaterial) {
            const colorMap = {
                IDLE: 0x00f0ff,
                LISTENING: 0x0088ff,
                THINKING: 0xb038ff,
                SPEAKING: 0x00e5ff,
                ALERT: 0xff003b,
            };
            wireframeMaterial.color.setHex(colorMap[currentState] || 0x00f0ff);
        }

        if (currentState === "LISTENING") {
            switch3DAnimation("Wake_Alert");
        } else if (currentState === "IDLE") {
            switch3DAnimation("Idle_Breath");
        }
    }

    // ==========================================================================
    // 6. THREE.JS 3D HOLOGRAPHIC CYBER-DOG (AXL + BOLT FUSION)
    // ==========================================================================
    let threeRenderer = null;
    let threeScene = null;
    let threeCamera = null;
    let threeClock = null;
    let threeMixer = null;
    let threeActions = {};
    let activeAction = null;
    let dogMeshGroup = null;
    let wireframeMaterial = null;
    let isThreeReady = false;

    function initThree() {
        if (!window.THREE || !window.THREE.GLTFLoader) {
            console.log("[Three.js] 3D libraries not loaded, running in pure 2D procedural HUD mode.");
            return;
        }

        const stage = document.getElementById("three-stage");
        if (!stage) return;

        try {
            threeScene = new THREE.Scene();
            threeClock = new THREE.Clock();

            const rect = stage.getBoundingClientRect();
            threeCamera = new THREE.PerspectiveCamera(42, rect.width / rect.height, 0.1, 100);
            threeCamera.position.set(0, 1.0, 2.4);
            threeCamera.lookAt(0, 0.45, 0);

            threeRenderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
            threeRenderer.setSize(rect.width, rect.height);
            threeRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
            stage.appendChild(threeRenderer.domElement);

            wireframeMaterial = new THREE.MeshBasicMaterial({
                color: 0x00f0ff,
                wireframe: true,
            });

            const loader = new THREE.GLTFLoader();
            loader.load(
                "assets/axl_dog_demo.glb",
                (gltf) => {
                    dogMeshGroup = gltf.scene;

                    dogMeshGroup.traverse((child) => {
                        if (child.isMesh) {
                            child.material = wireframeMaterial;
                        }
                    });

                    // Centered angled pose towards user
                    dogMeshGroup.position.set(0, -0.05, 0);
                    dogMeshGroup.rotation.y = Math.PI * 0.15;
                    threeScene.add(dogMeshGroup);

                    // Setup skeletal animations
                    if (gltf.animations && gltf.animations.length > 0) {
                        threeMixer = new THREE.AnimationMixer(dogMeshGroup);
                        gltf.animations.forEach((clip) => {
                            threeActions[clip.name] = threeMixer.clipAction(clip);
                        });

                        const defaultClip = threeActions["Idle_Breath"] || Object.values(threeActions)[0];
                        if (defaultClip) {
                            defaultClip.play();
                            activeAction = defaultClip;
                        }
                    }

                    isThreeReady = true;
                    console.log("[Three.js] A-X-L 3D Cybernetic Dog initialized! Actions:", Object.keys(threeActions));
                },
                undefined,
                (err) => {
                    console.log("[Three.js] 3D model not found, continuing with 2D radar HUD:", err);
                }
            );

            window.addEventListener("resize", () => {
                if (!threeCamera || !threeRenderer) return;
                const r = stage.getBoundingClientRect();
                threeCamera.aspect = r.width / r.height;
                threeCamera.updateProjectionMatrix();
                threeRenderer.setSize(r.width, r.height);
            });

        } catch (e) {
            console.warn("[Three.js] WebGL init failed:", e);
        }
    }

    function switch3DAnimation(actionName) {
        if (!threeMixer || !threeActions[actionName]) return;
        const targetAction = threeActions[actionName];
        if (targetAction === activeAction) return;

        if (activeAction) {
            activeAction.fadeOut(0.25);
        }
        targetAction.reset().fadeIn(0.25).play();
        activeAction = targetAction;
    }

    // ==========================================================================
    // 7. 60FPS PROCEDURAL VISOR HUD CANVAS RENDERER
    // ==========================================================================
    function renderVisor() {
        const width = canvas.width / window.devicePixelRatio;
        const height = canvas.height / window.devicePixelRatio;
        const cx = width / 2;
        const cy = height / 2;
        const maxRadius = Math.min(cx, cy) * 0.85;

        // Render 3D Model in WebGL if ready
        if (isThreeReady && threeRenderer && threeScene && threeCamera) {
            const delta = threeClock.getDelta();
            if (threeMixer) threeMixer.update(delta);
            if (dogMeshGroup) {
                // Subtle organic breathing sway
                dogMeshGroup.rotation.y = (Math.PI * 0.15) + Math.sin(animTime * 0.6) * 0.12;
            }
            threeRenderer.render(threeScene, threeCamera);
        }

        ctx.clearRect(0, 0, width, height);
        animTime += 0.02;

        // Theme Palette Selection
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

        // 4 Tactical Cardinal Ticks
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
            // Radial Equalizer Bars (A-X-L Speaking)
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
            // Expanding Sonic Waves (Listening to Voice)
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
            // Neural Cogwheels / Orbiting Nodes
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

        // Central Cybernetic Hound Iris / Core Dot (Only drawn if 3D model is not loaded)
        if (!isThreeReady) {
            const corePulse = Math.sin(animTime * 2) * 4;
            ctx.fillStyle = primaryColor;
            ctx.beginPath();
            ctx.arc(cx, cy, Math.max(4, coreRadius * 0.35 + corePulse), 0, Math.PI * 2);
            ctx.fill();
        }

        ctx.restore();
        animFrameId = requestAnimationFrame(renderVisor);
    }

    // Start everything
    connectWebSocket();
    initThree();
    renderVisor();

})();