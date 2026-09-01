/* Panel de administracion: GET/PUT /api/config, test IA, modelos. */
'use strict';

const AdminPanel = {
    init() {
        const toggle = document.getElementById('btn-admin-toggle');
        const panel = document.getElementById('admin-panel');
        toggle.addEventListener('click', () => {
            panel.classList.toggle('hidden');
            document.getElementById('admin-arrow').textContent =
                panel.classList.contains('hidden') ? '▸' : '▾';
            if (!panel.classList.contains('hidden')) this.reload();
        });

        document.getElementById('btn-test-ai').addEventListener('click', () => this.testAI());
        document.getElementById('btn-save-config').addEventListener('click', () => this.save());
    },

    status(text, isError = false) {
        const el = document.getElementById('admin-status');
        el.textContent = text;
        el.className = `text-xs font-mono ${isError ? 'text-red-400' : 'text-slate-400'}`;
    },

    async reload() {
        try {
            const resp = await fetch('/api/config');
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const cfg = await resp.json();
            document.getElementById('admin-ai-url').value = cfg.ai_base_url;
            document.getElementById('admin-model-name').value = cfg.ai_model_name;
            document.getElementById('admin-timeout').value = cfg.ai_timeout_seconds;
            document.getElementById('admin-retries').value = cfg.ai_max_retries;
            document.getElementById('admin-loglevel').value = cfg.log_level;

            // Rellenar select de modelos del panel rapido
            const modelSelect = document.getElementById('model-select');
            modelSelect.innerHTML = '<option value="">(detectar)</option>';
            this._loadModels(cfg.ai_base_url, modelSelect, cfg.ai_model_name);

            this.status(`Config cargada. WS conexiones: ${cfg.active_ws_connections}`);
        } catch (err) {
            this.status('Error cargando config: ' + err.message, true);
        }
    },

    async _loadModels(baseUrl, select, selectedModel) {
        try {
            const resp = await fetch('/api/ai/models');
            if (!resp.ok) return;
            const data = await resp.json();
            (data.data || []).forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                opt.textContent = m.id;
                select.appendChild(opt);
            });
            if (selectedModel) select.value = selectedModel;
        } catch (e) {
            console.log('[Admin] No se pudo listar modelos IA:', e);
        }
    },

    async testAI() {
        this.status('Probando conexion IA...');
        try {
            const resp = await fetch('/api/config/test-ai-connection', { method: 'POST' });
            const data = await resp.json();
            if (data.ok) {
                this.status(`IA OK en ${data.base_url} — modelos: ${data.models.join(', ') || '(sin listar)'}`);
                if (window.wsClient) {
                    window.wsClient._setServiceStatus('ai-status', 'online', 'IA: Online');
                }
            } else {
                this.status('IA FAIL: ' + data.detail, true);
                if (window.wsClient) {
                    window.wsClient._setServiceStatus('ai-status', 'offline', 'IA: Offline');
                }
            }
        } catch (err) {
            this.status('Error: ' + err.message, true);
        }
    },

    async save() {
        this.status('Guardando configuracion...');
        const station = document.getElementById('station-select')?.value || 'ADMIN';
        const body = {
            ai_base_url: document.getElementById('admin-ai-url').value.trim(),
            ai_model_name: document.getElementById('admin-model-name').value.trim(),
            ai_timeout_seconds: Number(document.getElementById('admin-timeout').value),
            ai_max_retries: Number(document.getElementById('admin-retries').value),
            log_level: document.getElementById('admin-loglevel').value,
            updated_by: station,
        };
        try {
            const resp = await fetch('/api/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.status);
            this.status('Configuracion guardada (broadcast CONFIG_UPDATED emitido)');
            if (window.wsClient) {
                window.wsClient.showNotification('Configuracion actualizada por admin', 'info');
            }
        } catch (err) {
            this.status('Error guardando: ' + err.message, true);
        }
    },
};
