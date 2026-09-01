/* Logica de inspeccion: captura -> POST /api/inspection -> render. */
'use strict';

const InspectionFlow = {
    camera: null,
    currentInspection: null,

    init() {
        this.camera = new CameraManager(document.getElementById('camera-video'));
        this._bindEvents();
        this._loadPersistedIdentity();
    },

    _bindEvents() {
        document.getElementById('btn-capture').addEventListener('click', async () => {
            const status = document.getElementById('capture-status');
            try {
                status.textContent = 'Capturando...';
                const blob = await this.camera.capture();
                this._showCapturedPreview(blob);
                status.textContent = 'Imagen capturada — listo para inspeccionar';
            } catch (err) {
                status.textContent = 'Error: ' + err.message;
            }
        });

        const startBtn = document.getElementById('btn-start');
        startBtn.addEventListener('click', () => this.startInspection());

        document.getElementById('serial-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.startInspection();
            }
        });

        // Blink de cursor mientras se escribe el serial (UX escaner)
        const serialInput = document.getElementById('serial-input');
        const blink = document.getElementById('serial-blink');
        serialInput.addEventListener('input', () => {
            blink.classList.remove('hidden');
            setTimeout(() => blink.classList.add('hidden'), 800);
        });

        document.getElementById('btn-force-pass').addEventListener('click', () =>
            this.override('PASS'));
        document.getElementById('btn-force-fail').addEventListener('click', () =>
            this.override('FAIL'));

        // Boton de estacion: al cambiar, reconecta WS con nueva identificacion
        document.getElementById('station-select').addEventListener('change', () => this._syncWsIdentity());
        document.getElementById('operator-input').addEventListener('change', () => this._syncWsIdentity());
    },

    _loadPersistedIdentity() {
        const stationSel = document.getElementById('station-select');
        const stations = ['RECEPCION-01', 'QA-01', 'INSPECCION-01'];
        const saved = localStorage.getItem('vs_station') || stations[0];
        stations.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s;
            opt.textContent = s;
            stationSel.appendChild(opt);
        });
        stationSel.value = saved;

        const op = document.getElementById('operator-input');
        op.value = localStorage.getItem('vs_operator') || '';

        op.addEventListener('change', () => {
            localStorage.setItem('vs_operator', op.value.trim());
            this._syncWsIdentity();
        });
        stationSel.addEventListener('change', () => {
            localStorage.setItem('vs_station', stationSel.value);
        });
    },

    _syncWsIdentity() {
        const station = document.getElementById('station-select').value;
        const operator = document.getElementById('operator-input').value.trim();
        localStorage.setItem('vs_station', station);
        localStorage.setItem('vs_operator', operator);
        if (window.wsClient) {
            window.wsClient.configure(station, operator);
        }
    },

    _showCapturedPreview(blob) {
        const img = document.getElementById('result-image');
        const placeholder = document.getElementById('result-placeholder');
        const url = URL.createObjectURL(blob);
        img.src = url;
        img.classList.remove('hidden');
        if (placeholder) placeholder.classList.add('hidden');
    },

    stationId() {
        return document.getElementById('station-select').value;
    },

    operatorId() {
        return document.getElementById('operator-input').value.trim() || null;
    },

    async startInspection() {
        const status = document.getElementById('capture-status');
        const stateEl = document.getElementById('inspection-state');
        const serial = document.getElementById('serial-input').value.trim().toUpperCase();
        const startBtn = document.getElementById('btn-start');

        if (!serial) {
            status.textContent = 'Escanea o escribe el numero de serie';
            document.getElementById('serial-input').focus();
            return;
        }
        if (!this.camera.ready) {
            status.textContent = 'Camara no disponible — usa una imagen existente?';
            return;
        }

        let blob;
        try {
            blob = await this.camera.capture();
        } catch (err) {
            status.textContent = 'Error captura: ' + err.message;
            return;
        }

        stateEl.textContent = 'PROCESANDO...';
        stateEl.className = 'font-mono text-amber-300';
        startBtn.disabled = true;
        this._setResultUnknown();

        const formData = new FormData();
        formData.append('image', blob, 'socket.png');
        formData.append('serial_number', serial);
        formData.append('socket_type', document.getElementById('socket-select').value);
        formData.append('station_id', this.stationId());
        if (this.operatorId()) formData.append('operator_id', this.operatorId());

        try {
            const resp = await fetch('/api/inspection', { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) {
                throw new Error(data.detail || `HTTP ${resp.status}`);
            }
            this._renderResult(data);
            stateEl.textContent = data.status === 'FAIL' ? 'DEFECTO' : 'COMPLETADO';
            stateEl.className = 'font-mono ' + (data.status === 'FAIL' ? 'text-red-400' : 'text-green-400');
            status.textContent = `OK en ${(data.inference_time_ms / 1000).toFixed(1)}s`;
        } catch (err) {
            console.error('[Inspection]', err);
            stateEl.textContent = 'ERROR';
            stateEl.className = 'font-mono text-red-400';
            status.textContent = 'Error: ' + err.message;
            if (window.wsClient) {
                window.wsClient.showAlert(`Error de inspeccion: ${err.message}`, 'danger');
            }
        } finally {
            startBtn.disabled = false;
            document.getElementById('last-inspection-time').textContent =
                new Date().toLocaleTimeString();
        }
    },

    _renderResult(data) {
        this.currentInspection = data;

        const img = document.getElementById('result-image');
        img.src = data.annotated_image_url + '?t=' + Date.now();
        img.classList.remove('hidden');
        document.getElementById('result-placeholder').classList.add('hidden');

        const badge = document.getElementById('result-status');
        badge.textContent = data.status;
        badge.className = 'badge badge-' + data.status.toLowerCase();

        document.getElementById('result-defects').textContent = data.defect_count;
        document.getElementById('result-confidence').textContent =
            Math.round(data.confidence * 100) + '%';
        document.getElementById('result-time').textContent =
            (data.inference_time_ms / 1000).toFixed(1) + 's';
        document.getElementById('result-socket').textContent =
            data.socket_type_detected || '--';

        const locList = document.getElementById('result-locations');
        locList.innerHTML = '';
        (data.defect_locations || []).forEach(loc => {
            const li = document.createElement('li');
            li.textContent = loc;
            locList.appendChild(li);
        });

        document.getElementById('result-observations').textContent = data.observations || '';

        const evBtn = document.getElementById('btn-view-evidence');
        evBtn.href = data.annotated_image_url;
        evBtn.classList.remove('hidden');

        const inspId = data.inspection_id;
        document.getElementById('btn-force-pass').dataset.id = inspId;
        document.getElementById('btn-force-fail').dataset.id = inspId;
        document.getElementById('btn-force-pass').disabled = false;
        document.getElementById('btn-force-fail').disabled = false;
    },

    _setResultUnknown() {
        const badge = document.getElementById('result-status');
        badge.textContent = '...';
        badge.className = 'badge badge-unknown';
        ['result-defects', 'result-confidence', 'result-time', 'result-socket']
            .forEach(id => document.getElementById(id).textContent = '--');
        document.getElementById('result-locations').innerHTML = '';
        document.getElementById('result-observations').textContent = '';
        document.getElementById('btn-force-pass').disabled = true;
        document.getElementById('btn-force-fail').disabled = true;
    },

    async override(disposition) {
        const inspId = this.currentInspection?.inspection_id;
        if (!inspId) return;
        const reason = prompt(`Motivo del override ${disposition} (opcional):`) || '';
        try {
            const resp = await fetch(`/api/inspections/${inspId}/override`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    disposition,
                    reason: reason || null,
                    operator_id: this.operatorId(),
                }),
            });
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.status);
            this.currentInspection.status = disposition;
            this._renderResult({ ...this.currentInspection, status: disposition });
            if (window.wsClient) {
                window.wsClient.showNotification(
                    `Override manual aplicado: ${disposition}`, 'warning');
            }
        } catch (err) {
            alert('Override fallido: ' + err.message);
        }
    },

    onRemoteCompleted(data) {
        // Inspeccion finalizada desde OTRA estacion: notificacion ya emitida
        console.log('[Inspection] Remoto:', data.serial, data.status);
    },
};
