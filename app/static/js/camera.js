/* Gestion de camara USB del cliente via WebRTC getUserMedia. */
'use strict';

class CameraManager {
    constructor(videoElement) {
        this.video = videoElement;
        this.stream = null;
        this.canvas = document.createElement('canvas');
        this.ready = false;
    }

    async start() {
        const status = document.getElementById('capture-status');
        const placeholder = document.getElementById('camera-placeholder');
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 1920 }, height: { ideal: 1080 } },
                audio: false,
            });
            this.video.srcObject = this.stream;
            await this.video.play();
            this.ready = true;
            if (placeholder) placeholder.classList.add('hidden');
            if (status) status.textContent = 'Camara lista';
            const btn = document.getElementById('btn-capture');
            if (btn) btn.disabled = false;
            return true;
        } catch (err) {
            console.error('[Camera] Error getUserMedia:', err);
            if (status) status.textContent = 'Error de camara: ' + err.message;
            if (placeholder) placeholder.textContent = 'Sin acceso a camara. Verifica permisos.';
            this.ready = false;
            return false;
        }
    }

    stop() {
        if (this.stream) {
            this.stream.getTracks().forEach(t => t.stop());
            this.stream = null;
            this.ready = false;
        }
    }

    /** Captura el frame actual como Blob PNG. */
    capture() {
        if (!this.ready || this.video.readyState < 2) {
            throw new Error('Camara no lista');
        }
        const w = this.video.videoWidth;
        const h = this.video.videoHeight;
        this.canvas.width = w;
        this.canvas.height = h;
        const ctx = this.canvas.getContext('2d');
        ctx.drawImage(this.video, 0, 0, w, h);
        return new Promise((resolve, reject) => {
            this.canvas.toBlob(
                blob => blob ? resolve(blob) : reject(new Error('Fallo al capturar')),
                'image/png'
            );
        });
    }
}
