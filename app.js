let API_URL = "http://localhost:5501";
let isBackendAvailable = false;

const app = {
    video: document.getElementById('video'),
    canvas: document.getElementById('canvas'),
    ctx: null,
    clips: [],
    currentClip: null,
    captions: [],
    isPlaying: false,
    isExporting: false,
    panA: 0.5,
    panB: 0.5,
    layoutMode: 'fill',
    brand: { img: null, text: '' },
    captionStyle: { color: '#ffffff', font: 'Inter' },
    activeCaptionIndex: -1,
    uploadedFilename: '',
    backendCheckPromise: null,
    theme: 'dark',
    toastContainer: null,

    init() {
        this.ctx = this.canvas.getContext('2d');
        this.toastContainer = document.getElementById('toast-container');
        this.loadTheme();
        this.backendCheckPromise = this.checkBackend().finally(() => this.updateCaptionHint());
        this.setupEvents();
        this.renderLoop();
    },

    loadTheme() {
        const stored = localStorage.getItem('gwura_theme');
        if (stored) this.theme = stored;
        document.documentElement.setAttribute('data-theme', this.theme);
        this.updateThemeIcon();
    },

    toggleTheme() {
        this.theme = this.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', this.theme);
        localStorage.setItem('gwura_theme', this.theme);
        this.updateThemeIcon();
    },

    updateThemeIcon() {
        document.getElementById('icon-sun').style.display = this.theme === 'dark' ? 'none' : 'block';
        document.getElementById('icon-moon').style.display = this.theme === 'dark' ? 'block' : 'none';
    },

    showToast(message, type = 'error') {
        if (!this.toastContainer) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerText = message;
        this.toastContainer.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 200);
        }, 3500);
    },

    setLoading(active, text = 'PROCESSING') {
        const loader = document.getElementById('loader');
        const loaderText = document.getElementById('loader-text');
        if (!loader || !loaderText) return;
        loader.style.display = active ? 'flex' : 'none';
        loaderText.innerText = text;
    },

    setExporting(isExporting) {
        this.isExporting = isExporting;
        const btn = document.getElementById('btn-export');
        if (btn) {
            btn.disabled = isExporting || !this.currentClip;
            btn.textContent = isExporting ? 'EXPORTING…' : 'EXPORT CLIP';
            btn.classList.toggle('exporting', isExporting);
        }
    },

    setTab(tabName) {
        document.querySelectorAll('.tab-btn').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.tab === tabName);
        });
        document.querySelectorAll('.tab-content').forEach((content) => {
            content.classList.toggle('active', content.id === `tab-${tabName}`);
        });
    },

    setLayout(mode) {
        this.layoutMode = mode;
        document.querySelectorAll('.layout-opt').forEach((opt) => {
            opt.classList.toggle('active', opt.dataset.layout === mode);
        });
    },

    async checkBackend() {
        const ports = [5501, 5500];
        for (const port of ports) {
            const url = `http://localhost:${port}`;
            try {
                const res = await fetch(url, { method: 'GET' });
                if (!res.ok) throw new Error('Backend unreachable');
                let health;
                try {
                    health = await res.json();
                } catch (jsonError) {
                    throw new Error('Invalid backend response');
                }

                API_URL = url;
                isBackendAvailable = true;
                const badge = document.getElementById('backend-status');
                if (badge) {
                    const aiState = health.ai_supported
                        ? health.ai_loaded
                            ? 'AI READY'
                            : 'AI PENDING'
                        : 'AI DISABLED';
                    badge.innerText = `SERVER ONLINE (${port}) • ${aiState}`;
                    badge.classList.add('online');
                    badge.title = health.ai_supported
                        ? 'Backend server is online. AI model will load on first analyze.'
                        : 'Backend server is online but AI is disabled.';
                }
                console.log(`Backend detected on ${url}`);
                return true;
            } catch (e) {
                console.log(`Backend probe failed on ${url}:`, e.message || e);
            }
        }

        isBackendAvailable = false;
        const badge = document.getElementById('backend-status');
        if (badge) {
            badge.innerText = 'OFFLINE MODE';
            badge.classList.remove('online');
            badge.title = 'Run the backend server on port 5501 to enable AI transcription and clip analysis.';
        }
        console.log('Offline mode: backend not found');
        return false;
    },

    setupEvents() {
        const videoInput = document.getElementById('video-input');
        const uploadBtn = document.getElementById('upload-sermon-btn');
        const logoUpload = document.getElementById('logo-upload');
        const btnPlay = document.getElementById('btn-play');
        const btnExport = document.getElementById('btn-export');
        const scrubber = document.getElementById('scrubber');
        const panAInput = document.getElementById('pan-a');
        const panBInput = document.getElementById('pan-b');
        const brandText = document.getElementById('brand-text');
        const captionColor = document.getElementById('caption-color');
        const captionFont = document.getElementById('caption-font');
        const themeBtn = document.getElementById('theme-btn');
        const tabLayoutBtn = document.getElementById('tab-layout-btn');
        const tabCaptionsBtn = document.getElementById('tab-captions-btn');
        const tabBrandBtn = document.getElementById('tab-brand-btn');
        const layoutFill = document.getElementById('layout-fill');
        const layoutFit = document.getElementById('layout-fit');
        const layoutSplit = document.getElementById('layout-split');
        const clearCaptionsBtn = document.getElementById('clear-captions-btn');

        if (videoInput) {
            videoInput.addEventListener('change', (e) => {
                console.log('video-input change', e.target.files && e.target.files.length);
                this.handleUpload(e).finally(() => {
                    if (e.target) {
                        e.target.value = '';
                    }
                });
            });
            videoInput.addEventListener('click', (e) => e.stopPropagation());
        }

        // Wire the visible upload button to the hidden file input
        if (uploadBtn && videoInput) {
            uploadBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log('upload button clicked');
                videoInput.value = '';
                videoInput.click();
            });
        }

        this.video.onerror = () => {
            this.setLoading(false);
            this.showToast('Failed to load video file.', 'error');
        };

        if (logoUpload) {
            logoUpload.addEventListener('change', (e) => {
                const file = e.target.files[0];
                if (file) {
                    const img = new Image();
                    img.onload = () => this.brand.img = img;
                    img.src = URL.createObjectURL(file);
                }
            });
        }

        if (themeBtn) {
            themeBtn.addEventListener('click', () => this.toggleTheme());
        }

        if (tabLayoutBtn) {
            tabLayoutBtn.addEventListener('click', () => this.setTab('layout'));
        }
        if (tabCaptionsBtn) {
            tabCaptionsBtn.addEventListener('click', () => this.setTab('captions'));
        }
        if (tabBrandBtn) {
            tabBrandBtn.addEventListener('click', () => this.setTab('brand'));
        }

        if (layoutFill) {
            layoutFill.addEventListener('click', () => this.setLayout('fill'));
        }
        if (layoutFit) {
            layoutFit.addEventListener('click', () => this.setLayout('fit'));
        }
        if (layoutSplit) {
            layoutSplit.addEventListener('click', () => this.setLayout('split'));
        }

        if (clearCaptionsBtn) {
            clearCaptionsBtn.addEventListener('click', () => this.clearCaptions());
        }

        if (btnPlay) {
            btnPlay.addEventListener('click', () => this.togglePlay());
        }

        if (btnExport) {
            btnExport.addEventListener('click', () => this.exportClip());
        }

        if (scrubber) {
            scrubber.addEventListener('click', (e) => {
                if (!this.currentClip || !this.video.duration) return;
                const rect = e.currentTarget.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const pct = x / rect.width;
                const clipDur = this.currentClip.end - this.currentClip.start;
                this.video.currentTime = this.currentClip.start + pct * clipDur;
            });
        }

        if (panAInput) {
            panAInput.addEventListener('input', (e) => {
                this.panA = e.target.value / 100;
                document.getElementById('pan-val').innerText = `${e.target.value}%`;
            });
        }

        if (panBInput) {
            panBInput.addEventListener('input', (e) => this.panB = e.target.value / 100);
        }

        if (btnPlay) {
            btnPlay.addEventListener('click', () => this.togglePlay());
        }

        if (btnExport) {
            btnExport.addEventListener('click', () => this.exportClip());
        }

        if (scrubber) {
            scrubber.addEventListener('click', (e) => {
                if (!this.currentClip || !this.video.duration) return;
                const rect = e.currentTarget.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const pct = x / rect.width;
                const clipDur = this.currentClip.end - this.currentClip.start;
                this.video.currentTime = this.currentClip.start + pct * clipDur;
            });
        }

        if (panAInput) {
            panAInput.addEventListener('input', (e) => {
                this.panA = e.target.value / 100;
                document.getElementById('pan-val').innerText = `${e.target.value}%`;
            });
        }

        if (panBInput) {
            panBInput.addEventListener('input', (e) => this.panB = e.target.value / 100);
        }

        if (brandText) {
            brandText.addEventListener('input', (e) => this.brand.text = e.target.value);
        }

        if (captionColor) {
            captionColor.addEventListener('input', (e) => this.captionStyle.color = e.target.value);
        }

        if (captionFont) {
            captionFont.addEventListener('change', (e) => this.captionStyle.font = e.target.value);
        }

        this.video.addEventListener('timeupdate', () => this.onTimeUpdate());
        this.video.addEventListener('ended', () => { this.isPlaying = false; });
    },

    fmtTime(seconds, showMs = false) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        if (showMs) {
            const ms = Math.floor((seconds % 1) * 10);
            return `${mins}:${secs.toString().padStart(2, '0')}.${ms}`;
        }
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    },

    async handleUpload(e) {
        if (e && typeof e.preventDefault === 'function') {
            e.preventDefault();
        }
        const file = e.target?.files?.[0];
        console.log('handleUpload', !!file, e?.target?.files?.length);
        if (!file) {
            this.showToast('No video file selected.', 'error');
            return;
        }

        this.video.src = URL.createObjectURL(file);
        this.setLoading(true, 'ANALYZING...');
        this.uploadedFilename = '';

        if (this.backendCheckPromise) {
            await this.backendCheckPromise;
        }

        if (!isBackendAvailable) {
            await this.checkBackend();
            this.updateCaptionHint();
        }

        if (isBackendAvailable) {
            try {
                await this.analyzeBackend(file);
            } catch (error) {
                console.error('analyzeBackend failed', error);
                this.showToast(error.message || 'Backend analysis failed.', 'error');
                this.setLoading(false);
            }
        } else {
            if (file.size > 500 * 1024 * 1024) {
                this.setLoading(false);
                this.showToast('File too large for browser analysis. Please use the backend server.', 'error');
                return;
            }
            this.showToast('Backend unavailable, using local browser analysis.', 'success');
            this.analyzeLocal(file);
        }
    },

    async analyzeBackend(file) {
        const formData = new FormData();
        formData.append('file', file);
        const uploadRes = await fetch(`${API_URL}/upload`, { method: 'POST', body: formData });
        if (!uploadRes.ok) {
            const message = await uploadRes.text();
            throw new Error(`Upload failed: ${message || uploadRes.statusText}`);
        }

        const uploadData = await uploadRes.json();
        this.uploadedFilename = uploadData.filename;

        const analyzeForm = new FormData();
        analyzeForm.append('filename', this.uploadedFilename);
        const res = await fetch(`${API_URL}/analyze`, { method: 'POST', body: analyzeForm });
        if (!res.ok) {
            const message = await res.text();
            throw new Error(`Backend analysis failed: ${message || res.statusText}`);
        }

        const data = await res.json();
        this.clips = data.clips.map((c) => ({ ...c, tagClass: this.getTagClass(c.type) }));
        this.renderClips();
        if (this.clips.length) {
            this.loadClip(0);
        } else {
            this.showToast('No clips were found. Try a different sermon or check the backend logs.', 'error');
        }
        this.setLoading(false);
        this.setExporting(false);
    },

    async analyzeLocal(file) {
        try {
            await new Promise((resolve) => {
                if (this.video.readyState >= 1) return resolve();
                this.video.onloadedmetadata = () => resolve();
            });
            const duration = this.video.duration || 120;
            this.generateSimulatedClips(duration);
        } catch (e) {
            console.error(e);
            this.generateSimulatedClips(120);
        }
    },

    generateSimulatedClips(duration) {
        this.clips = [];
        const count = Math.min(8, Math.max(3, Math.floor(duration / 45)));
        const spacing = duration / (count + 1);
        const baseTarget = Math.max(45, Math.min(120, Math.floor(duration / (count + 1))));

        for (let i = 0; i < count; i++) {
            const type = i % 2 === 0 ? 'Viral Short' : 'Mini Sermon';
            const variation = [0, 15, 30, 45, 60, 90, 120][Math.min(i, 6)];
            const clipLength = Math.min(240, Math.max(30, baseTarget + variation));
            const targetStart = Math.max(0, Math.floor((i + 1) * spacing - clipLength / 2));
            const targetEnd = Math.min(duration, targetStart + clipLength);

            this.clips.push({
                id: i + 1,
                title: `Clip ${i + 1}: ${type}`,
                type,
                tagClass: this.getTagClass(type),
                start: targetStart,
                end: targetEnd,
                duration: targetEnd - targetStart,
                score: Math.max(50, 95 - i * 3),
                caption_preview: 'Add caption...'
            });
        }

        this.renderClips();
        this.loadClip(0);
        this.setLoading(false);
        this.setExporting(false);
    },

    getTagClass(type) {
        if (type.includes('Viral')) return 'tag-viral';
        if (type.includes('Motiv') || type.includes('Reel')) return 'tag-motivational';
        return 'tag-scripture';
    },

    renderClips() {
        const list = document.getElementById('clip-list');
        if (!list) return;
        list.innerHTML = '';

        if (!this.clips || this.clips.length === 0) {
            list.innerHTML = `
                <div style="text-align:center; padding:2rem; color:var(--text-muted); font-size:0.85rem; line-height:1.5;">
                    No clips were generated yet.<br>Upload a sermon and wait for the analysis to complete.<br>If the backend is offline, the browser will simulate clips.
                </div>
            `;
            return;
        }

        const fragment = document.createDocumentFragment();

        this.clips.forEach((clip, idx) => {
            const div = document.createElement('div');
            div.className = 'clip-item';
            div.innerHTML = `
                <div class="clip-score">${clip.score}</div>
                <div style="font-weight:700; font-size:0.9rem; color:var(--text-primary);">${clip.title}</div>
                <div style="font-size:0.75rem; color:var(--text-muted); margin-top:4px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    <span class="clip-tag ${clip.tagClass}">${clip.type}</span>
                    <span>${this.fmtTime(clip.duration ?? (clip.end - clip.start), true)}</span>
                    <span style="opacity:0.75;">${this.fmtTime(clip.start)} - ${this.fmtTime(clip.end)}</span>
                </div>
            `;
            div.addEventListener('click', () => this.loadClip(idx));
            fragment.appendChild(div);
        });

        list.appendChild(fragment);
    },

    loadClip(idx) {
        if (!this.clips[idx]) return;
        this.currentClip = this.clips[idx];
        if (this.video.duration) {
            this.video.currentTime = this.currentClip.start;
        }

        document.querySelectorAll('.clip-item').forEach((el) => el.classList.remove('active'));
        const selected = document.querySelectorAll('.clip-item')[idx];
        if (selected) selected.classList.add('active');

        const clipEl = document.getElementById('scrubber-clip');
        if (clipEl && this.video.duration) {
            const clipStartPct = (this.currentClip.start / this.video.duration) * 100;
            const clipWidthPct = ((this.currentClip.end - this.currentClip.start) / this.video.duration) * 100;
            clipEl.style.left = `${clipStartPct}%`;
            clipEl.style.width = `${clipWidthPct}%`;
        }

        this.generateCaptions();
        this.setExporting(false);
    },

    generateCaptions() {
        const dur = this.currentClip.end - this.currentClip.start;
        const count = Math.max(1, Math.ceil(dur / 3));
        this.captions = [];
        for (let i = 0; i < count; i++) {
            this.captions.push({
                time: this.currentClip.start + i * 3,
                text: i === 0 && this.currentClip.caption_preview !== 'Add caption...' ? this.currentClip.caption_preview : 'Type caption here...'
            });
        }
        this.activeCaptionIndex = -1;
        this.renderCaptionEditor();
    },
    updateCaptionHint() {
        const hint = document.getElementById('caption-hint');
        if (!hint) return;

        if (isBackendAvailable) {
            hint.innerHTML = 'Backend available: upload the video and wait for AI analysis to generate transcript captions and better clip timing.';
        } else {
            hint.innerHTML = 'Offline Mode: Auto-transcription is disabled. Placeholders are provided for timing. Run the backend server on port 5501 to enable transcripts.';
        }
    },
    renderCaptionEditor() {
        const list = document.getElementById('caption-list');
        if (!list) return;
        list.innerHTML = '';

        this.captions.forEach((cap, idx) => {
            const row = document.createElement('div');
            row.className = 'caption-row';
            row.dataset.idx = idx;

            const time = document.createElement('div');
            time.className = 'caption-time';
            time.innerText = this.fmtTime(cap.time);

            const input = document.createElement('input');
            input.className = 'caption-input';
            input.value = cap.text;
            input.addEventListener('input', (event) => this.updateCaption(idx, event.target.value));

            row.appendChild(time);
            row.appendChild(input);
            row.addEventListener('click', (e) => {
                if (e.target.tagName !== 'INPUT') {
                    this.video.currentTime = cap.time;
                }
            });
            list.appendChild(row);
        });
    },

    updateCaption(idx, val) {
        if (this.captions[idx]) {
            this.captions[idx].text = val;
        }
    },

    clearCaptions() {
        this.captions = this.captions.map((c) => ({ ...c, text: '' }));
        this.renderCaptionEditor();
    },

    togglePlay() {
        if (this.video.paused) {
            this.video.play();
        } else {
            this.video.pause();
        }
    },

    onTimeUpdate() {
        const t = this.video.currentTime;
        const dur = this.video.duration;
        if (dur) {
            const pct = (t / dur) * 100;
            const playhead = document.getElementById('playhead');
            if (playhead) playhead.style.left = `${pct}%`;
            document.getElementById('time-display').innerText = this.fmtTime(t);
        }

        if (this.currentClip && !this.isExporting) {
            if (t >= this.currentClip.end) {
                this.video.currentTime = this.currentClip.start;
            }
        }

        let newActiveIndex = -1;
        this.captions.forEach((cap, i) => {
            const next = this.captions[i + 1]?.time || cap.time + 3;
            if (t >= cap.time && t < next) {
                newActiveIndex = i;
            }
        });

        if (newActiveIndex !== this.activeCaptionIndex) {
            this.activeCaptionIndex = newActiveIndex;
            document.querySelectorAll('.caption-row').forEach((row) => row.classList.remove('active'));
            const activeRow = document.querySelector(`.caption-row[data-idx='${newActiveIndex}']`);
            if (activeRow) {
                activeRow.classList.add('active');
                activeRow.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        }
    },

    drawWrappedText(ctx, text, x, y, maxWidth, lineHeight) {
        if (!text || text === 'Type caption here...') return;

        const words = text.split(' ');
        let line = '';
        const lines = [];

        for (let n = 0; n < words.length; n++) {
            const testLine = line + words[n] + ' ';
            const metrics = ctx.measureText(testLine);
            if (metrics.width > maxWidth && n > 0) {
                lines.push(line.trim());
                line = `${words[n]} `;
            } else {
                line = testLine;
            }
        }
        if (line) lines.push(line.trim());

        lines.forEach((lineText, idx) => {
            ctx.strokeText(lineText, x, y + idx * lineHeight);
            ctx.fillText(lineText, x, y + idx * lineHeight);
        });
    },

    renderLoop() {
        requestAnimationFrame(() => this.renderLoop());
        const w = this.canvas.width;
        const h = this.canvas.height;
        this.ctx.clearRect(0, 0, w, h);
        this.ctx.fillStyle = '#000';
        this.ctx.fillRect(0, 0, w, h);

        if (!this.video || this.video.readyState < 2) {
            return;
        }

        const vw = this.video.videoWidth;
        const vh = this.video.videoHeight;
        const targetRatio = 9 / 16;
        const cropW = vh * targetRatio;

        if (this.layoutMode === 'fill') {
            const cropX = Math.max(0, Math.min(vw - cropW, (vw - cropW) * this.panA));
            this.ctx.drawImage(this.video, cropX, 0, cropW, vh, 0, 0, w, h);
        } else if (this.layoutMode === 'fit') {
            this.ctx.filter = 'blur(40px) brightness(0.6)';
            this.ctx.drawImage(this.video, 0, 0, w, h);
            this.ctx.filter = 'none';
            const scale = w / vw;
            const drawH = vh * scale;
            const drawY = (h - drawH) / 2;
            this.ctx.drawImage(this.video, 0, drawY, w, drawH);
        } else if (this.layoutMode === 'split') {
            const splitH = h / 2;
            const cropSize = Math.min(vw, vh);
            const srcXA = Math.max(0, Math.min(vw - cropSize, (vw - cropSize) * this.panA));
            const srcXB = Math.max(0, Math.min(vw - cropSize, (vw - cropSize) * this.panB));
            this.ctx.drawImage(this.video, srcXA, 0, cropSize, cropSize, 0, 0, w, splitH);
            this.ctx.drawImage(this.video, srcXB, 0, cropSize, cropSize, 0, splitH, w, splitH);
            this.ctx.fillStyle = '#000';
            this.ctx.fillRect(0, splitH - 2, w, 4);
        }

        const t = this.video.currentTime;
        const cap = this.captions.find((c, i) => {
            const next = this.captions[i + 1]?.time || c.time + 3;
            return t >= c.time && t < next;
        });

        if (cap) {
            this.ctx.save();
            const fontSize = 56;
            this.ctx.font = `bold ${fontSize}px ${this.captionStyle.font}`;
            this.ctx.textAlign = 'center';
            this.ctx.fillStyle = this.captionStyle.color;
            this.ctx.strokeStyle = 'black';
            this.ctx.lineWidth = 6;
            this.ctx.shadowColor = 'rgba(0,0,0,0.8)';
            this.ctx.shadowBlur = 10;
            this.drawWrappedText(this.ctx, cap.text, w / 2, h * 0.7, w * 0.8, fontSize * 1.2);
            this.ctx.restore();
        }

        if (this.brand.img) {
            const lw = 120;
            const lh = (this.brand.img.height / this.brand.img.width) * lw;
            this.ctx.drawImage(this.brand.img, w - lw - 40, 60, lw, lh);
        }

        if (this.brand.text) {
            this.ctx.font = '600 32px Inter';
            this.ctx.fillStyle = 'white';
            this.ctx.textAlign = 'center';
            this.ctx.fillText(this.brand.text, w / 2, h - 80);
        }
    },

    async exportClip() {
        if (!this.currentClip) {
            this.showToast('Select a clip before exporting.', 'error');
            return;
        }

        this.setLoading(true, 'EXPORTING...');
        this.setExporting(true);

        const captionText = this.captions.map((c) => c.text).join(' ');

        if (isBackendAvailable && this.uploadedFilename) {
            const form = new FormData();
            form.append('filename', this.uploadedFilename);
            form.append('start', this.currentClip.start);
            form.append('end', this.currentClip.end);
            form.append('pan_x', this.panA);
            form.append('caption', captionText);

            try {
                const res = await fetch(`${API_URL}/render`, { method: 'POST', body: form });
                if (!res.ok) {
                    throw new Error('Render failed.');
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `gwura_export.mp4`;
                a.click();
                this.showToast('Export ready for download.', 'success');
            } catch (e) {
                this.showToast(`Error: ${e.message}`, 'error');
            } finally {
                this.setLoading(false);
                this.setExporting(false);
            }
        } else {
            try {
                if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                    this.showToast('An export is already in progress.', 'error');
                    this.setLoading(false);
                    this.setExporting(false);
                    return;
                }

                const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
                    ? 'video/webm;codecs=vp9'
                    : 'video/webm';

                const stream = this.canvas.captureStream(30);
                if (!stream) {
                    throw new Error('Screen capture not supported in this browser.');
                }

                const audioStream = this.video.captureStream ? this.video.captureStream() : null;
                if (audioStream) {
                    const audioTrack = audioStream.getAudioTracks()[0];
                    if (audioTrack) {
                        stream.addTrack(audioTrack);
                    }
                }

                this.mediaRecorder = new MediaRecorder(stream, { mimeType });
                const chunks = [];
                this.mediaRecorder.ondataavailable = (event) => chunks.push(event.data);
                this.mediaRecorder.onstop = () => {
                    const blob = new Blob(chunks, { type: mimeType });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'gwura_browser_export.webm';
                    a.click();
                    this.mediaRecorder = null;
                    this.setLoading(false);
                    this.setExporting(false);
                    this.showToast('Browser export complete.', 'success');
                };
                this.mediaRecorder.onerror = (event) => {
                    console.error('MediaRecorder error', event);
                    this.mediaRecorder = null;
                    this.setLoading(false);
                    this.setExporting(false);
                    this.showToast('Browser export failed.', 'error');
                };

                this.video.currentTime = this.currentClip.start;
                this.video.muted = false;
                await this.video.play();
                this.mediaRecorder.start();

                const durationMs = (this.currentClip.end - this.currentClip.start) * 1000;
                setTimeout(() => {
                    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                        this.mediaRecorder.stop();
                        this.video.pause();
                    }
                }, durationMs);
            } catch (e) {
                this.showToast(`Browser export error: ${e.message}`, 'error');
                this.setLoading(false);
                this.setExporting(false);
            }
        }
    }
};

window.addEventListener('DOMContentLoaded', () => app.init());
