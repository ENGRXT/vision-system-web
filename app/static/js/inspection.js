/* Logica de inspeccion: captura -> POST /api/inspection -> render. */
'use strict';

const InspectionFlow = {
    camera: null,
    currentInspection: null,
    _inspectionSeq: 0,

    init() {
        this.camera = new CameraManager(document.getElementById('camera-video'));
        this._bindEvents();
        this._loadPersistedIdentity();
        this._startCamera();
    },

    _startCamera() {
        const retryBtn = document.getElementById('btn-camera-retry');
        if (retryBtn) retryBtn.classList.add('hidden');
        this.camera.start().then(ok => {
            if (!ok && retryBtn) retryBtn.classList.remove('hidden');
        });
    },

    _bindEvents() {
        document.getElementById('btn-camera-retry').addEventListener('click', () => this._startCamera());

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

        // Marca esta inspeccion como la mas reciente: si el operador arranca
        // otra antes de que esta termine, la respuesta obsoleta se descarta
        // y no pisa el estado/imagen de la inspeccion nueva.
        const mySeq = ++this._inspectionSeq;

        const socketHint = document.getElementById('socket-select').value;
        const formData = new FormData();
        formData.append('image', blob, 'socket.png');
        formData.append('serial_number', serial);
        formData.append('socket_type', socketHint);
        formData.append('station_id', this.stationId());
        if (this.operatorId()) formData.append('operator_id', this.operatorId());

        try {
            const resp = await fetch('/api/inspection', { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) {
                throw new Error(data.detail || `HTTP ${resp.status}`);
            }
            if (mySeq !== this._inspectionSeq) {
                // Ya hay una inspeccion mas reciente: descarto este resultado
                console.warn('[Inspection] Respuesta obsoleta descartada (serial', serial + ')');
                return;
            }
            data.socket_selected = socketHint;
            this._renderResult(data);
            stateEl.textContent = data.status === 'FAIL' ? 'DEFECTO' : 'COMPLETADO';
            stateEl.className = 'font-mono ' + (data.status === 'FAIL' ? 'text-red-400' : 'text-green-400');
            status.textContent = `OK en ${(data.inference_time_ms / 1000).toFixed(1)}s`;
        } catch (err) {
            if (mySeq !== this._inspectionSeq) return;
            console.error('[Inspection]', err);
            stateEl.textContent = 'ERROR';
            stateEl.className = 'font-mono text-red-400';
            status.textContent = 'Error: ' + err.message;
            if (window.wsClient) {
                window.wsClient.showAlert(`Error de inspeccion: ${err.message}`, 'danger');
            }
        } finally {
            // Solo la inspeccion mas reciente habilita el boton y marca la hora
            if (mySeq === this._inspectionSeq) {
                startBtn.disabled = false;
                document.getElementById('last-inspection-time').textContent =
                    new Date().toLocaleTimeString();
            }
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

        // Estacion propietaria del resultado (guarda anti-confusion):
        // si el resultado llego de OTRA estacion, se marca en rojo con un
        // aviso explicito en vez de mostrarlo como si fuera local.
        const stationEl = document.getElementById('result-station');
        const localStation = (this.stationId() || 'UNKNOWN').toUpperCase();
        const resultStation = (data.station_id || localStation).toUpperCase();
        stationEl.textContent = resultStation;
        const mismatch = !!data.station_id && resultStation !== localStation;
        stationEl.className = 'font-mono text-sm ' +
            (mismatch ? 'text-red-400' : 'text-slate-200');
        document.getElementById('result-station-warn').classList.toggle('hidden', !mismatch);
        if (mismatch && window.wsClient) {
            window.wsClient.showAlert(
                `AVISO: este resultado es de la estacion ${data.station_id} ` +
                `(tu estacion es ${localStation})`,
                'danger');
        }

        // Socket: tipo detectado por IA + tipo seleccionado por el operador
        let socketText = data.socket_type_detected || '--';
        const sel = data.socket_selected;
        if (sel && sel !== 'AUTO') socketText += ` (seleccionado: ${sel})`;
        document.getElementById('result-socket').textContent = socketText;
        document.getElementById('result-pins').textContent =
            (data.pin_count !== null && data.pin_count !== undefined) ? data.pin_count : '--';

        // Metodo de deteccion: HYBRID (medicion CV + verificacion IA),
        // CV (solo medicion, IA sin servicio) o VLM (camino clasico)
        document.getElementById('result-detection-method').textContent =
            data.detection_method || '--';

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

        // Trazabilidad (aislamiento S5): serial + ID de inspeccion. La imagen
        // y el zoom son de ESTA inspeccion (URL unica por carpeta de evidencia).
        document.getElementById('result-serial').textContent =
            data.serial_number || data.serial || '--';
        const inspId = data.inspection_id;
        const idEl = document.getElementById('result-insp-id');
        idEl.textContent = inspId ? String(inspId).slice(0, 8) : '--';
        idEl.title = String(inspId || '');

        const cropBtn = document.getElementById('btn-socket-crop');
        if (data.socket_crop_url) {
            cropBtn.href = data.socket_crop_url;
            cropBtn.classList.remove('hidden');
        } else {
            cropBtn.classList.add('hidden');
        }

        const cvBtn = document.getElementById('btn-cv-analysis');
        if (data.cv_analysis_url) {
            cvBtn.href = data.cv_analysis_url;
            cvBtn.classList.remove('hidden');
        } else {
            cvBtn.classList.add('hidden');
        }

        document.getElementById('btn-force-pass').dataset.id = inspId;
        document.getElementById('btn-force-fail').dataset.id = inspId;
        document.getElementById('btn-force-pass').disabled = false;
        document.getElementById('btn-force-fail').disabled = false;
    },

    _setResultUnknown() {
        const badge = document.getElementById('result-status');
        badge.textContent = '...';
        badge.className = 'badge badge-unknown';
        ['result-defects', 'result-confidence', 'result-time', 'result-socket',
          'result-pins', 'result-detection-method']
            .forEach(id => document.getElementById(id).textContent = '--');
        const stationEl = document.getElementById('result-station');
        stationEl.textContent = '--';
        stationEl.className = 'font-mono text-sm text-slate-200';
        document.getElementById('result-station-warn').classList.add('hidden');
        document.getElementById('result-serial').textContent = '--';
        const idEl = document.getElementById('result-insp-id');
        idEl.textContent = '--';
        idEl.title = '';
        document.getElementById('btn-socket-crop').classList.add('hidden');
        document.getElementById('btn-cv-analysis').classList.add('hidden');
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
