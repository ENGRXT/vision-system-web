/* Cliente WebSocket nativo con reconexion automatica y auto-sanado.
 * Especificacion: VISION-SYSTEM WEB v4 — seccion 5.7.
 *
 * Mejoras de robustez (incidente multi-estacion):
 * - La identificacion (estacion) se aplica ANTES de conectar: configure()
 *   reconecta siempre que la estacion cambie, incluso si la conexion
 *   anterior estaba en curso. Corregido: las estaciones quedaban
 *   conectadas como "UNKNOWN" porque la 1a conexion salia antes de cargar
 *   la estacion y configure() no reintentaba con el socket en CONNECTING.
 * - Watchdog: sin datos (msgs/pong) en 75s => cierre forzado + reconexion.
 *   Evita sockets "zombi" a medias tras reinicios del servidor.
 * - Al volver la pestana a primer plano, si el socket no esta abierto se
 *   reconecta de inmediato (navegadores en background dejan sockets
 *   colgados y el indicador se quedaba en rojo sin reintentar).
 * - Backoff exponencial 1s -> 30s, se reinicia al conectar.
 * - Estados visibles: Conectando... / Conectado / Sin conexion.
 */
'use strict';

class WebSocketClient {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.stationId = 'UNKNOWN';
        this.operatorId = '';
        this._connectedStation = null; // estacion de la conexion activa
        this._closedByUser = false;
        this._reconnectDelay = 1000;
        this._maxReconnectDelay = 30000;
        this._reconnectTimer = null;
        this._lastDataAt = 0;
        this._watchdogTimer = null;
        this._pingTimer = null;
        // NO conectar solo: App.init() llama a configure() al cargar la
        // estacion, y esa primera conexion ya lleva la identidad correcta.
        // Red de seguridad: si configure() nunca llega (error de JS en
        // otro script), conectar con lo que se lea del select.
        setTimeout(() => {
            if (!this.ws) {
                const sel = document.getElementById('station-select');
                const op = document.getElementById('operator-input');
                this.configure(sel ? sel.value : 'UNKNOWN', op ? op.value.trim() : '');
            }
        }, 1500);
    }

    // Aplica identificacion y garantiza conexion con ella. Si ya hay
    // conexion abierta con la misma estacion, no hace nada.
    configure(stationId, operatorId) {
        const station = (stationId || 'UNKNOWN').toUpperCase();
        const operator = operatorId || '';
        const identityChanged = station !== this.stationId;
        this.stationId = station;
        this.operatorId = operator;

        const st = this.ws ? this.ws.readyState : WebSocket.CLOSED;
        const needsConnect =
            !this.ws ||
            st === WebSocket.CLOSED ||
            st === WebSocket.CLOSING ||
            identityChanged;
        if (needsConnect) {
            this._connectNow();
        }
        // Caso CONNECTING con la misma estacion: dejar terminar el handshake.
    }

    _buildUrl() {
        const params = new URLSearchParams({
            station_id: this.stationId,
            operator_id: this.operatorId,
        });
        return `${this.url}?${params.toString()}`;
    }

    _connectNow() {
        // Cancela reconexiones pendientes y aborta cualquier conexion
        // en curso (p. ej. iniciada con identidad antigua).
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this.ws) {
            const old = this.ws;
            old.onopen = null;
            old.onmessage = null;
            old.onclose = null;
            old.onerror = null;
            if (old.readyState === WebSocket.OPEN || old.readyState === WebSocket.CONNECTING) {
                try { old.close(); } catch { /* ya cerrada */ }
            }
        }
        this.ws = null;
        this._stopPing();
        this._stopWatchdog();
        this.showStatus('connecting');

        let socket;
        try {
            socket = new WebSocket(this._buildUrl());
        } catch (err) {
            console.error('[WS] No se pudo crear el socket:', err);
            this._scheduleReconnect();
            return;
        }
        this.ws = socket;
        this._connectedStation = this.stationId;

        socket.onopen = () => {
            if (this.ws !== socket) return; // obsoleta
            this._reconnectDelay = 1000;
            this._lastDataAt = Date.now();
            console.log('[WS] Conectado como', this.stationId);
            this.showStatus('online');
            this._startPing();
            this._startWatchdog();
        };

        socket.onmessage = (event) => {
            if (this.ws !== socket) return; // obsoleta
            this._lastDataAt = Date.now();
            if (event.data === 'pong') return;
            let message;
            try {
                message = JSON.parse(event.data);
            } catch {
                return;
            }
            this.handleMessage(message);
        };

        socket.onclose = () => {
            if (this.ws !== socket) return; // obsoleta
            console.log('[WS] Desconectado, reconectando...');
            this.showStatus('offline');
            this._stopPing();
            this._stopWatchdog();
            this.ws = null;
            this._scheduleReconnect();
        };

        socket.onerror = (error) => {
            console.error('[WS] Error:', error);
            // onclose siempre sigue a onerror; alli se programa la reconexion.
        };
    }

    _scheduleReconnect() {
        if (this._reconnectTimer) return;
        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            const delay = this._reconnectDelay;
            this._reconnectDelay = Math.min(delay * 2, this._maxReconnectDelay);
            this._connectNow();
        }, this._reconnectDelay);
    }

    _startPing() {
        this._stopPing();
        this._pingTimer = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                try { this.ws.send('ping'); } catch { /* socket en cierre */ }
            }
        }, 25000);
    }

    _stopPing() {
        if (this._pingTimer) {
            clearInterval(this._pingTimer);
            this._pingTimer = null;
        }
    }

    // Watchdog: el servidor corta a los 60s sin datos y el cliente pings
    // cada 25s, asi en normal siempre hay actividad. Si aun asi pasan
    // 75s sin nada, la conexion esta "a medias" (zombi): forzar cierre.
    _startWatchdog() {
        this._stopWatchdog();
        this._watchdogTimer = setInterval(() => {
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
            if (Date.now() - this._lastDataAt > 75000) {
                console.warn('[WS] 75s sin datos — forzando reconexion');
                try { this.ws.close(); } catch { /* ya cerrada */ }
            }
        }, 10000);
    }

    _stopWatchdog() {
        if (this._watchdogTimer) {
            clearInterval(this._watchdogTimer);
            this._watchdogTimer = null;
        }
    }

    showStatus(state) {
        const el = document.getElementById('ws-status');
        if (!el) return;
        if (state === 'online') {
            el.className = 'status-pill status-online';
            el.textContent = 'WS: Conectado';
        } else if (state === 'connecting') {
            el.className = 'status-pill status-connecting';
            el.textContent = 'WS: Conectando...';
        } else {
            el.className = 'status-pill status-offline';
            el.textContent = 'WS: Sin conexion';
        }
    }

    // Verdadero solo si el evento pertenece a la estacion local
    _isLocalStation(data) {
        const sid = (data && data.station_id || '').toLowerCase();
        const local = (this.stationId || '').toLowerCase();
        return !!sid && sid === local;
    }

    handleMessage(message) {
        const { event: eventType, data, timestamp } = message;
        switch (eventType) {
            case 'INSPECTION_STARTED':
                // El backend solo lo envia a la estacion propia; filtro de seguridad extra
                if (this._isLocalStation(data)) {
                    this.showNotification(
                        `Inspeccion iniciada: ${data.serial}`, 'info');
                }
                break;
            case 'INSPECTION_COMPLETED':
                if (this._isLocalStation(data)) {
                    this.showNotification(
                        `Inspeccion ${data.serial} completada: ${data.status}`, 'info');
                    if (window.InspectionFlow) {
                        window.InspectionFlow.onRemoteCompleted(data);
                    }
                }
                break;
            case 'INSPECTION_OVERRIDDEN':
                if (this._isLocalStation(data)) {
                    this.showNotification(
                        `Override manual: ${data.serial} -> ${data.disposition}`, 'warning');
                }
                break;
            case 'DEFECT_DETECTED':
                // CRITICO: solo se muestra imagen/sonido si el defecto es de
                // ESTA estacion. Sin este filtro, una estacion veria el
                // resultado (y la imagen) de otra trabajando en paralelo.
                if (this._isLocalStation(data)) {
                    this.showAlert(
                        `DEFECTO: ${data.serial} — ${data.defect_count} defecto(s)`,
                        'danger');
                    this.playAlertSound();
                    this.showDefectImage(data.image_url);
                }
                break;
            case 'AI_SERVICE_DOWN':
                this.showAlert('Servicio de IA CAIDO', 'warning');
                this._setServiceStatus('ai-status', 'offline', 'IA: Offline');
                break;
            case 'AI_SERVICE_RECOVERED':
                this.showNotification('Servicio de IA recuperado', 'success');
                this._setServiceStatus('ai-status', 'online', 'IA: Online');
                break;
            case 'STATION_CONNECTED':
                this.updateStationStatus(data.station_id, 'online');
                break;
            case 'STATION_DISCONNECTED':
                this.updateStationStatus(data.station_id, 'offline');
                break;
            case 'CONFIG_UPDATED': {
                const fields = Object.keys(data.changes || {}).join(', ');
                this.showNotification(`Configuracion actualizada (${fields})`, 'info');
                if (window.AdminPanel) window.AdminPanel.reload();
                break;
            }
            default:
                this.showNotification(`[${eventType}]`, 'info');
        }
        if (window.App && window.App.onWSEvent) window.App.onWSEvent(message);
    }

    _setServiceStatus(id, state, label) {
        const el = document.getElementById(id);
        if (!el) return;
        el.className = `status-pill ${state === 'online' ? 'status-online' : 'status-offline'}`;
        el.textContent = label;
    }

    updateStationStatus(stationId, state) {
        console.log(`[WS] Estacion ${stationId} ${state}`);
        const panel = document.getElementById('station-feed');
        if (!panel) return;
        const row = panel.querySelector(`[data-station="${CSS.escape(stationId)}"]`);
        if (row) {
            row.dataset.state = state;
            row.querySelector('.dot').className =
                `dot ${state === 'online' ? 'dot-online' : 'dot-offline'}`;
            row.querySelector('.label').textContent = state === 'online' ? 'online' : 'offline';
        }
    }

    showAlert(text, level) {
        const panel = document.getElementById('notification-panel');
        if (!panel) return;
        const placeholder = panel.querySelector('.text-slate-500');
        if (placeholder) placeholder.remove();
        const div = document.createElement('div');
        div.className = `alert alert-${level}`;
        const strong = document.createElement('strong');
        strong.textContent = `[${new Date().toLocaleTimeString()}] `;
        div.appendChild(strong);
        div.appendChild(document.createTextNode(text));
        panel.prepend(div);
        while (panel.children.length > 20) panel.lastChild.remove();
    }

    showNotification(text, level) {
        this.showAlert(text, level === 'danger' ? 'danger' : (level || 'info'));
    }

    showDefectImage(imageUrl) {
        const img = document.getElementById('result-image');
        const placeholder = document.getElementById('result-placeholder');
        if (!img || !imageUrl) return;
        img.src = imageUrl + (imageUrl.includes('?') ? '&' : '?') + 't=' + Date.now();
        img.classList.remove('hidden');
        if (placeholder) placeholder.classList.add('hidden');
    }

    playAlertSound() {
        const audio = new Audio('/static/assets/alert.mp3');
        audio.play().catch(() => {
            this._beepFallback();
        });
    }

    _beepFallback() {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'square';
            osc.frequency.value = 880;
            gain.gain.value = 0.2;
            osc.connect(gain).connect(ctx.destination);
            osc.start();
            setTimeout(() => { osc.stop(); ctx.close(); }, 600);
        } catch (e) {
            console.log('[WS] Audio bloqueado', e);
        }
    }
}

// Auto-sanado al volver a primer plano: los navegadores suspenden
// temporizadores en pestanas en background y pueden dejar el socket
// colgado; si la pestana se activa sin conexion, reconectar de inmediato.
document.addEventListener('visibilitychange', () => {
    const client = window.wsClient;
    if (!client || document.visibilityState !== 'visible') return;
    if (!client.ws) {
        if (client._reconnectTimer) {
            clearTimeout(client._reconnectTimer);
            client._reconnectTimer = null;
        }
        client._connectNow();
        return;
    }
    const st = client.ws.readyState;
    if (st === WebSocket.OPEN && Date.now() - client._lastDataAt > 30000) {
        // Abierta "en papel" pero sin datos: socket zombi, forzar cierre
        console.warn('[WS] Pestana activa, socket sin datos — reconexion');
        try { client.ws.close(); } catch { /* ya cerrada */ }
    } else if (st === WebSocket.CLOSED || st === WebSocket.CLOSING) {
        if (client._reconnectTimer) {
            clearTimeout(client._reconnectTimer);
            client._reconnectTimer = null;
        }
        client._connectNow();
    }
    // CONNECTING: dejar terminar el handshake.
});

document.addEventListener('DOMContentLoaded', () => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const serverHost = window.location.host;
    window.wsClient = new WebSocketClient(`${proto}://${serverHost}/ws/notifications`);
});
