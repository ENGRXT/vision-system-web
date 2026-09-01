/* Entry point: bootstrap de la SPA + health polling. */
'use strict';

const App = {
    HEALTH_INTERVAL_MS: 15000,
    _healthTimer: null,

    init() {
        InspectionFlow.init();
        AdminPanel.init();

        // Sincronizar identificacion (estacion/operador) con el WS
        this._syncWsIdentity();
        this._pollHealth();
        this._healthTimer = setInterval(() => this._pollHealth(), this.HEALTH_INTERVAL_MS);

        console.log('[App] VISION-SYSTEM WEB v4.0 inicializado');
    },

    _syncWsIdentity() {
        const station = document.getElementById('station-select').value;
        const operator = document.getElementById('operator-input').value.trim();
        if (window.wsClient) {
            window.wsClient.configure(station, operator);
        }
    },

    async _pollHealth() {
        try {
            const resp = await fetch('/health/ready');
            const data = await resp.json();
            this._setStatus('ai-status', data.checks?.ai_service === 'ok',
                'IA: Online', 'IA: Offline');
            this._setStatus('db-status', data.checks?.database === 'ok',
                'DB: OK', 'DB: Error');
        } catch {
            this._setStatus('ai-status', false, 'IA: --', 'IA: --');
            this._setStatus('db-status', false, 'DB: --', 'DB: --');
        }
    },

    _setStatus(id, ok, labelOn, labelOff) {
        const el = document.getElementById(id);
        if (!el) return;
        el.className = `status-pill ${ok ? 'status-online' : 'status-offline'}`;
        el.textContent = ok ? labelOn : labelOff;
    },

    onWSEvent(message) {
        // Hook extensible para futuros dashboards (B-001)
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
