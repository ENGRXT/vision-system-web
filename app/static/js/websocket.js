/* Cliente WebSocket nativo con reconexion automatica.
 * Especificacion: VISION-SYSTEM WEB v4 — seccion 5.7.
 */
'use strict';

class WebSocketClient {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.reconnectInterval = 3000;
        this.stationId = 'UNKNOWN';
        this.operatorId = '';
        this._closedByUser = false;
        this.connect();
    }

    configure(stationId, operatorId) {
        this.stationId = stationId || 'UNKNOWN';
        this.operatorId = operatorId || '';
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this._closedByUser = true;
            this.ws.close();
            this._closedByUser = false;
            this.connect();
        }
    }

    _buildUrl() {
        const params = new URLSearchParams({
            station_id: this.stationId,
            operator_id: this.operatorId,
        });
        return `${this.url}?${params.toString()}`;
    }

    connect() {
        this.ws = new WebSocket(this._buildUrl());

        this.ws.onopen = () => {
            console.log('[WS] Conectado');
            this.showStatus('online');
            this._startPing();
        };

        this.ws.onmessage = (event) => {
            let message;
            try {
                message = JSON.parse(event.data);
            } catch {
                if (event.data === 'pong') return;
                return;
            }
            this.handleMessage(message);
        };

        this.ws.onclose = () => {
            console.log('[WS] Desconectado, reconectando...');
            this.showStatus('offline');
            this._stopPing();
            if (!this._closedByUser) {
                setTimeout(() => this.connect(), this.reconnectInterval);
            }
        };

        this.ws.onerror = (error) => {
            console.error('[WS] Error:', error);
        };
    }

    _startPing() {
        this._stopPing();
        this._pingTimer = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send('ping');
            }
        }, 25000);
    }

    _stopPing() {
        if (this._pingTimer) {
            clearInterval(this._pingTimer);
            this._pingTimer = null;
        }
    }

    showStatus(state) {
        const el = document.getElementById('ws-status');
        if (!el) return;
        el.className = `status-pill ${state === 'online' ? 'status-online' : 'status-offline'}`;
        el.textContent = state === 'online' ? 'WS: Conectado' : 'WS: Sin conexion';
    }

    handleMessage(message) {
        const { event: eventType, data, timestamp } = message;
        switch (eventType) {
            case 'INSPECTION_STARTED':
                this.showNotification(
                    `Inspeccion iniciada: ${data.serial} (${data.station_id})`, 'info');
                break;
            case 'INSPECTION_COMPLETED':
                this.showNotification(
                    `Inspeccion ${data.serial} completada: ${data.status}`, 'info');
                if (window.InspectionFlow) {
                    window.InspectionFlow.onRemoteCompleted(data);
                }
                break;
            case 'INSPECTION_OVERRIDDEN':
                this.showNotification(
                    `Override manual: ${data.serial} -> ${data.disposition}`, 'warning');
                break;
            case 'DEFECT_DETECTED':
                this.showAlert(
                    `DEFECTO en ${data.station_id}: ${data.serial} — ${data.defect_count} pines`,
                    'danger');
                this.playAlertSound();
                this.showDefectImage(data.image_url);
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

document.addEventListener('DOMContentLoaded', () => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const serverHost = window.location.host;
    window.wsClient = new WebSocketClient(`${proto}://${serverHost}/ws/notifications`);
});
