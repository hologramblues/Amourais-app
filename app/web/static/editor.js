        // ============================================
        // MESSAGES — plus aucune boîte système
        // --------------------------------------------
        // Cet écran portait les 8 derniers alert() de l'application :
        // le Viewer et le Calendrier avaient déjà remplacé les leurs
        // par un dialogue local. Une boîte système vole le focus, ne
        // se style pas, et bloque le fil pendant un export FFmpeg.
        //
        // `note()` passe par l'implémentation partagée de
        // samourais-app.js (role=status, non bloquante, aux couleurs
        // du thème). Le repli console garantit qu'aucun message n'est
        // perdu si la couche partagée n'a pas encore été analysée.
        // ============================================
        function note(message, kind) {
            if (window.samourais && window.samourais.notify) {
                return window.samourais.notify(message, kind || 'error');
            }
            console.warn('[editor]', message);
        }

        // ============================================
        // TEMPLATES DEFINITION
        // ============================================
        const TEMPLATES = {
            square: {
                width: 1080,
                height: 1080,
                frame: {
                    x: 54,          // 5% padding
                    y: 195,         // ~18% from top
                    width: 972,     // 90% width
                    height: 810,    // 75% height
                    radius: 27      // 2.5% corner radius
                },
                textArea: {
                    x: 54,
                    y: 40,
                    width: 972,
                    maxY: 180       // Don't go below this
                },
                watermark: {
                    x: 1010,
                    y: 1040
                }
            },
            portrait: {
                width: 1080,
                height: 1350,
                frame: {
                    x: 54,
                    y: 220,
                    width: 972,
                    height: 1020,
                    radius: 27
                },
                textArea: {
                    x: 54,
                    y: 40,
                    width: 972,
                    maxY: 200
                },
                watermark: {
                    x: 1010,
                    y: 1300
                }
            },
            story: {
                width: 1080,
                height: 1920,
                frame: {
                    x: 108,         // 10% padding — bigger so Instagram Reels crop doesn't eat content
                    y: 350,
                    width: 864,     // 80% width (1080 - 2*108)
                    height: 1300,
                    radius: 27
                },
                textArea: {
                    x: 108,
                    y: 280,
                    width: 864,
                    maxY: 330
                },
                watermark: {
                    x: 956,         // shifted left to match new margin
                    y: 1700
                }
            }
        };

        // ============================================
        // STATE
        // ============================================
        const state = {
            currentTemplate: 'square',
            // Media state (image or video)
            mediaType: null, // 'image' or 'video'
            imageSrc: null,
            imageName: '',
            imageSize: 0,
            imageScale: 100,
            imageOffsetX: 0,
            imageOffsetY: 0,
            // Video-specific state
            videoFile: null,
            videoDuration: 0,
            trimStart: 0,
            trimEnd: 0,
            isPlaying: false,
            // Text state
            text: '',
            textSize: 42,
            lineHeight: 1.2,
            overlayText: '',
            showOverlay: false,
            // Watermark state
            watermarkOpacity: 100,
            // Frame customization (for story template)
            frameHeightPercent: 100,
            // Canvas state
            scale: 1,
            // ---- Retouche image (LOT C) ----
            // Tout est appliqué par Fabric côté client : rien ne part au
            // serveur, donc aucun fichier temporaire à nettoyer et aucun
            // échec silencieux possible sur ces opérations.
            cropRatio: null,      // null = image entière, sinon largeur/hauteur
            rotation: 0,          // 0 | 90 | 180 | 270 (degrés, sens horaire)
            flipX: false,
            flipY: false,
            brightness: 0,        // -100..100 → filtre Fabric -1..1
            contrast: 0,          // -100..100
            saturation: 0,        // -100..100
            // ---- Fichier de sortie (LOT C) ----
            exportFormat: 'png',  // 'png' | 'jpeg'
            exportQuality: 90,    // 50..100, JPEG uniquement
            exportScale: 1        // multiplicateur de la taille du template
        };

        // Valeurs de départ des réglages de retouche — sert au bouton
        // « Annuler » et au chargement d'une nouvelle image.
        const IMAGE_EDIT_DEFAULTS = {
            cropRatio: null, rotation: 0, flipX: false, flipY: false,
            brightness: 0, contrast: 0, saturation: 0
        };

        // ============================================
        // DOM ELEMENTS
        // ============================================
        const uploadZone = document.getElementById('upload-zone');
        const fileInput = document.getElementById('file-input');
        const memeTextInput = document.getElementById('meme-text');
        const textSizeSlider = document.getElementById('text-size');
        const textSizeValue = document.getElementById('text-size-value');
        const lineHeightSlider = document.getElementById('line-height');
        const lineHeightValue = document.getElementById('line-height-value');
        const imageScaleSection = document.getElementById('image-scale-section');
        const imageScaleSlider = document.getElementById('image-scale');
        const imageScaleValue = document.getElementById('image-scale-value');
        const frameHeightSection = document.getElementById('frame-height-section');
        const frameHeightSlider = document.getElementById('frame-height');
        const frameHeightValue = document.getElementById('frame-height-value');
        const selectImageBtn = document.getElementById('select-image-btn');
        const overlayToggle = document.getElementById('overlay-toggle');
        const overlaySwitch = document.getElementById('overlay-switch');
        const overlayTextInput = document.getElementById('overlay-text');
        const resetBtn = document.getElementById('reset-btn');
        const exportBtn = document.getElementById('export-btn');
        const scheduleBtn = document.getElementById('schedule-btn');
        const saveMemeBtn = document.getElementById('save-meme-btn');
        const formatBtns = document.querySelectorAll('.format-btn');
        
        // Import source elements
        const importTabs = document.querySelectorAll('.import-tab');
        const driveZone = document.getElementById('drive-zone');
        const driveConnect = document.getElementById('drive-connect');
        const driveLoading = document.getElementById('drive-loading');
        const driveFiles = document.getElementById('drive-files');
        const connectDriveBtn = document.getElementById('connect-drive-btn');
        
        // Watermark opacity
        const watermarkOpacitySlider = document.getElementById('watermark-opacity');
        const watermarkOpacityValue = document.getElementById('watermark-opacity-value');
        
        // Video-related elements
        const mediaTypeBadge = document.getElementById('media-type-badge');
        const timelineContainer = document.getElementById('timeline-container');
        const timelineWrapper = document.getElementById('timeline-wrapper');
        const timelineThumbnails = document.getElementById('timeline-thumbnails');
        const timelineSelection = document.getElementById('timeline-selection');
        const handleStart = document.getElementById('handle-start');
        const handleEnd = document.getElementById('handle-end');
        const timelinePlayhead = document.getElementById('timeline-playhead');
        const timeStartEl = document.getElementById('time-start');
        const timeEndEl = document.getElementById('time-end');
        const trimDurationEl = document.getElementById('trim-duration');
        const btnPlay = document.getElementById('btn-play');
        const btnPreview = document.getElementById('btn-preview');
        const videoSource = document.getElementById('video-source');

        // ---- Retouche image + fichier de sortie (LOT C) ----
        const imageTools = document.getElementById('image-tools');
        const imageResetBtn = document.getElementById('image-reset-btn');
        const cropGroup = document.getElementById('crop-group');
        const cropReadout = document.getElementById('crop-readout');
        const orientReadout = document.getElementById('orient-readout');
        const rotateLeftBtn = document.getElementById('rotate-left');
        const rotateRightBtn = document.getElementById('rotate-right');
        const flipHBtn = document.getElementById('flip-h');
        const flipVBtn = document.getElementById('flip-v');
        const adjBrightness = document.getElementById('adj-brightness');
        const adjBrightnessValue = document.getElementById('adj-brightness-value');
        const adjContrast = document.getElementById('adj-contrast');
        const adjContrastValue = document.getElementById('adj-contrast-value');
        const adjSaturation = document.getElementById('adj-saturation');
        const adjSaturationValue = document.getElementById('adj-saturation-value');
        const outputTools = document.getElementById('output-tools');
        const imgFormatGroup = document.getElementById('imgformat-group');
        const qualityCtl = document.getElementById('quality-ctl');
        const exportQuality = document.getElementById('export-quality');
        const exportQualityValue = document.getElementById('export-quality-value');
        const sizeGroup = document.getElementById('size-group');
        const exportDims = document.getElementById('export-dims');
        const exportFormatLabel = document.getElementById('export-format-label');

        // ============================================
        // FABRIC CANVAS
        // ============================================
        let canvas;
        let textBox, imageObj, overlayTextObj, frameRect, frameBorder, watermark, templateBg;
        let clipRect; // The fixed clipping rectangle

        // Canvas padding to show controls outside template - needs to be large enough for scaled images
        const CANVAS_PADDING = 350;

        // ============================================
        // FOND DU PLAN DE TRAVAIL — suit le thème
        // --------------------------------------------
        // Fabric peint son fond lui-même, en JS : il ne voit pas le CSS.
        // La valeur était écrite en dur ('#2a2a2a'), si bien qu'en thème
        // CLAIR cet écran affichait un pavé quasi noir de 712x712 px au
        // milieu d'une page à rgb(244,245,247) — le seul écran de
        // l'application dans ce cas.
        //
        // On lit le jeton --bg-2 (la « zone en creux » du socle), qui est
        // défini dans les DEUX thèmes, et on repeint à chaque bascule.
        // ============================================
        function artboardBackdrop() {
            const v = getComputedStyle(document.documentElement)
                .getPropertyValue('--bg-2').trim();
            return v || '#2a2a2a';   // repli : l'ancien fond
        }

        function repaintBackdrop() {
            if (!canvas) return;
            canvas.backgroundColor = artboardBackdrop();
            canvas.requestRenderAll();
        }

        function initCanvas() {
            canvas = new fabric.Canvas('meme-canvas', {
                backgroundColor: artboardBackdrop(),
                selection: true,
                preserveObjectStacking: true,
                perPixelTargetFind: false // Click on bounding box, not just visible pixels
            });

            // Custom controls style - larger and more visible
            fabric.Object.prototype.set({
                borderColor: '#ef4444',
                cornerColor: '#ef4444',
                cornerStrokeColor: '#ffffff',
                cornerSize: 16,
                cornerStyle: 'circle',
                transparentCorners: false,
                borderScaleFactor: 2,
                borderDashArray: [5, 5],
                padding: 10
            });

            updateCanvasSize();
            createElements();
            setupCanvasHoverEffects();
            setupSnapping();
        }

        function setupCanvasHoverEffects() {
            // Show border on hover
            canvas.on('mouse:over', function(e) {
                if (e.target && e.target.selectable) {
                    e.target._showBorder = true;
                    e.target.set('dirty', true);
                    canvas.renderAll();
                }
            });

            canvas.on('mouse:out', function(e) {
                if (e.target && e.target._showBorder) {
                    e.target._showBorder = false;
                    e.target.set('dirty', true);
                    canvas.renderAll();
                }
            });
        }

        // ============================================
        // SNAPPING / MAGNET SYSTEM
        // ============================================
        const SNAP_THRESHOLD = 15; // Distance in pixels to trigger snap
        let snapLines = []; // Visual guide lines

        function setupSnapping() {
            canvas.on('object:moving', function(e) {
                const obj = e.target;
                if (!obj) return;

                const template = TEMPLATES[state.currentTemplate];
                const frame = template.frame;
                const offset = CANVAS_PADDING;
                const textArea = template.textArea;

                // Define snap points (left edges, centers, right edges)
                const snapPointsX = [
                    offset + textArea.x,                           // Text area left
                    offset + frame.x,                              // Frame left
                    offset + template.width / 2,                   // Template center
                    offset + frame.x + frame.width / 2,            // Frame center
                    offset + frame.x + frame.width,                // Frame right
                    offset + template.width - textArea.x,          // Text area right (mirrored)
                ];

                const snapPointsY = [
                    offset + textArea.y,                           // Text area top
                    offset + frame.y,                              // Frame top
                    offset + template.height / 2,                  // Template center
                    offset + frame.y + frame.height,               // Frame bottom
                ];

                // Get object bounds
                const objLeft = obj.left;
                const objTop = obj.top;
                const objRight = obj.left + (obj.width * (obj.scaleX || 1));
                const objCenterX = obj.left + (obj.width * (obj.scaleX || 1)) / 2;
                const objCenterY = obj.top + (obj.height * (obj.scaleY || 1)) / 2;

                let snappedX = false;
                let snappedY = false;
                
                // Clear previous snap lines
                clearSnapLines();

                // Check X snapping (left edge)
                for (const snapX of snapPointsX) {
                    if (Math.abs(objLeft - snapX) < SNAP_THRESHOLD) {
                        obj.set('left', snapX);
                        snappedX = true;
                        showSnapLine('vertical', snapX);
                        break;
                    }
                }

                // Check X snapping (center) - only for text objects
                if (!snappedX && (obj === textBox || obj === overlayTextObj)) {
                    const templateCenterX = offset + template.width / 2;
                    if (Math.abs(objCenterX - templateCenterX) < SNAP_THRESHOLD) {
                        obj.set('left', templateCenterX - (obj.width * (obj.scaleX || 1)) / 2);
                        snappedX = true;
                        showSnapLine('vertical', templateCenterX);
                    }
                }

                // Check Y snapping (top edge)
                for (const snapY of snapPointsY) {
                    if (Math.abs(objTop - snapY) < SNAP_THRESHOLD) {
                        obj.set('top', snapY);
                        snappedY = true;
                        showSnapLine('horizontal', snapY);
                        break;
                    }
                }

                canvas.renderAll();
            });

            canvas.on('object:modified', function() {
                clearSnapLines();
                canvas.renderAll();
            });

            canvas.on('mouse:up', function() {
                clearSnapLines();
                canvas.renderAll();
            });
        }

        function showSnapLine(orientation, position) {
            const template = TEMPLATES[state.currentTemplate];
            const offset = CANVAS_PADDING;
            
            let line;
            if (orientation === 'vertical') {
                line = new fabric.Line([position, offset, position, offset + template.height], {
                    stroke: '#ef4444',
                    strokeWidth: 1,
                    strokeDashArray: [5, 3],
                    selectable: false,
                    evented: false,
                    opacity: 0.8
                });
            } else {
                line = new fabric.Line([offset, position, offset + template.width, position], {
                    stroke: '#ef4444',
                    strokeWidth: 1,
                    strokeDashArray: [5, 3],
                    selectable: false,
                    evented: false,
                    opacity: 0.8
                });
            }
            
            canvas.add(line);
            snapLines.push(line);
        }

        function clearSnapLines() {
            snapLines.forEach(line => canvas.remove(line));
            snapLines = [];
        }

        function updateCanvasSize() {
            const template = TEMPLATES[state.currentTemplate];
            const { width, height } = template;
            const container = document.querySelector('.preview-area');
            const maxW = container.clientWidth - 60;
            const maxH = container.clientHeight - 100;
            
            // Calculate scale based on template + padding
            const totalWidth = width + (CANVAS_PADDING * 2);
            const totalHeight = height + (CANVAS_PADDING * 2);
            
            state.scale = Math.min(maxW / totalWidth, maxH / totalHeight, 0.4);
            
            canvas.setWidth(totalWidth * state.scale);
            canvas.setHeight(totalHeight * state.scale);
            canvas.setZoom(state.scale);
        }

        function createElements() {
            canvas.clear();
            canvas.backgroundColor = artboardBackdrop();

            const template = TEMPLATES[state.currentTemplate];
            const frame = template.frame;
            const offset = CANVAS_PADDING; // Offset for all elements

            // Calculate effective frame height (for story template customization)
            const effectiveFrameHeight = Math.round(frame.height * (state.frameHeightPercent / 100));
            
            // Calculate Y position to keep frame centered
            const originalCenterY = frame.y + frame.height / 2;
            const effectiveFrameY = originalCenterY - effectiveFrameHeight / 2;

            // White template background
            templateBg = new fabric.Rect({
                left: offset,
                top: offset,
                width: template.width,
                height: template.height,
                fill: '#ffffff',
                selectable: false,
                evented: false
            });
            canvas.add(templateBg);

            // Create the clip path (absolute position - stays fixed)
            clipRect = new fabric.Rect({
                left: frame.x + offset,
                top: effectiveFrameY + offset,
                width: frame.width,
                height: effectiveFrameHeight,
                rx: frame.radius,
                ry: frame.radius,
                absolutePositioned: true
            });

            // Frame placeholder (gray background when no image)
            frameRect = new fabric.Rect({
                left: frame.x + offset,
                top: effectiveFrameY + offset,
                width: frame.width,
                height: effectiveFrameHeight,
                rx: frame.radius,
                ry: frame.radius,
                fill: '#f0f0f0',
                selectable: false,
                evented: false
            });
            canvas.add(frameRect);

            // Frame border - interactive indicator (shows where image area is)
            frameBorder = new fabric.Rect({
                left: frame.x + offset,
                top: effectiveFrameY + offset,
                width: frame.width,
                height: effectiveFrameHeight,
                rx: frame.radius,
                ry: frame.radius,
                fill: 'transparent',
                stroke: '#ddd',
                strokeWidth: 2,
                strokeDashArray: [8, 4],
                selectable: false,
                evented: false
            });
            canvas.add(frameBorder);

            // Text box
            const textArea = template.textArea;
            textBox = new fabric.Textbox(state.text || 'Tape ton texte...', {
                left: textArea.x + offset,
                top: textArea.y + offset,
                width: textArea.width,
                fontSize: state.textSize,
                fontFamily: 'Inter, Helvetica, Arial, sans-serif',
                fontWeight: '300',
                fill: '#000000',
                lineHeight: state.lineHeight,
                textAlign: 'left',
                splitByGrapheme: false,
                hasControls: true,
                cornerSize: 16,
                hoverCursor: 'move',
                moveCursor: 'move'
            });
            canvas.add(textBox);
            
            // Sync textBox changes back to state and input
            textBox.on('changed', function() {
                const newText = textBox.text === 'Tape ton texte...' ? '' : textBox.text;
                state.text = newText;
                memeTextInput.value = newText;
            });

            // Watermark (logo or text fallback)
            const wm = template.watermark;
            if (logoImage) {
                // Use logo image
                watermark = new fabric.Image(logoImage.getElement(), {
                    left: wm.x + offset,
                    top: wm.y + offset,
                    originX: 'right',
                    originY: 'bottom',
                    scaleX: 0.15,
                    scaleY: 0.15,
                    opacity: state.watermarkOpacity / 100,
                    selectable: true,
                    hasControls: true,
                    cornerSize: 16,
                    hoverCursor: 'move',
                    moveCursor: 'move'
                });
            } else {
                // Fallback to text if logo not loaded yet
                watermark = new fabric.Text('SAMOURAÏS', {
                    left: wm.x + offset,
                    top: wm.y + offset,
                    fontSize: template.width * 0.04,
                    fontFamily: 'Impact, sans-serif',
                    fontWeight: '800',
                    fill: '#ffffff',
                    stroke: '#333333',
                    strokeWidth: 2,
                    angle: -3,
                    originX: 'right',
                    originY: 'bottom',
                    opacity: state.watermarkOpacity / 100,
                    shadow: new fabric.Shadow({
                        color: 'rgba(0,0,0,0.3)',
                        blur: 4,
                        offsetX: 2,
                        offsetY: 2
                    }),
                    selectable: true,
                    hasControls: true,
                    cornerSize: 16,
                    hoverCursor: 'move',
                    moveCursor: 'move'
                });
            }
            canvas.add(watermark);

            canvas.renderAll();
        }

        // ============================================
        // MEDIA HANDLING (IMAGE + VIDEO)
        // ============================================
        function loadMedia(file) {
            const isVideo = file.type.startsWith('video/');
            state.mediaType = isVideo ? 'video' : 'image';
            
            // Update badge
            mediaTypeBadge.style.display = 'inline-block';
            mediaTypeBadge.textContent = isVideo ? 'Vidéo' : 'Image';
            mediaTypeBadge.className = `media-type-badge ${isVideo ? 'video' : 'image'}`;
            
            if (isVideo) {
                loadVideo(file);
            } else {
                loadImage(file);
            }
        }

        function loadImage(file) {
            const reader = new FileReader();
            reader.onload = (e) => {
                state.imageSrc = e.target.result;
                state.imageName = file.name;
                state.imageSize = file.size;
                state.imageScale = 100;
                state.imageOffsetX = 0;
                state.imageOffsetY = 0;
                // LOT C — une nouvelle image repart d'une retouche vierge :
                // garder le recadrage de la précédente n'aurait aucun sens.
                Object.assign(state, IMAGE_EDIT_DEFAULTS);
                syncImageEditControls();
                updateMediaToolsVisibility();

                // Hide video timeline
                timelineContainer.style.display = 'none';

                updateUploadZone();
                addImageToCanvas(e.target.result);
                
                imageScaleSection.style.display = 'block';
                imageScaleSlider.value = 100;
                imageScaleValue.textContent = '100%';
                selectImageBtn.style.display = 'block';
                
                exportBtn.disabled = false;
                exportBtn.textContent = '📥 Télécharger le meme';
                scheduleBtn.disabled = false;
                if (saveMemeBtn) saveMemeBtn.disabled = false;
            };
            reader.readAsDataURL(file);
        }

        function loadVideo(file) {
            state.videoFile = file;
            state.imageName = file.name;
            state.imageSize = file.size;
            state.imageScale = 100;
            state.imageOffsetX = 0;
            state.imageOffsetY = 0;
            // LOT C — la retouche et le format image ne s'appliquent pas à
            // une vidéo : on remet à zéro et on masque les deux blocs.
            Object.assign(state, IMAGE_EDIT_DEFAULTS);
            syncImageEditControls();
            updateMediaToolsVisibility();

            // Show loading state
            uploadZone.classList.add('has-file');
            uploadZone.innerHTML = `
                <div class="file-preview">
                    <div class="video-loading">
                        <div class="loading-spinner"></div>
                    </div>
                    <div class="file-info">
                        <div class="file-name">${file.name}</div>
                        <div class="file-size">Chargement de la vidéo...</div>
                    </div>
                </div>
            `;
            
            const url = URL.createObjectURL(file);
            videoSource.src = url;
            
            videoSource.onloadedmetadata = () => {
                state.videoDuration = videoSource.duration;
                state.trimStart = 0;
                state.trimEnd = Math.min(videoSource.duration, 30); // Max 30s default
                
                updateUploadZone();
                generateThumbnails();
                updateTimelineUI();
                
                // Show video timeline
                timelineContainer.style.display = 'block';
                imageScaleSection.style.display = 'block';
                imageScaleSlider.value = 100;
                imageScaleValue.textContent = '100%';
                selectImageBtn.style.display = 'block';
                
                // Capture first frame for canvas preview
                captureVideoFrame(0);
                
                exportBtn.disabled = false;
                exportBtn.textContent = '🎬 Exporter la vidéo';
                scheduleBtn.disabled = false;
                if (saveMemeBtn) saveMemeBtn.disabled = false;
            };

            videoSource.load();
        }

        function captureVideoFrame(time) {
            return new Promise((resolve) => {
                videoSource.currentTime = time;
                videoSource.onseeked = () => {
                    const tempCanvas = document.createElement('canvas');
                    tempCanvas.width = videoSource.videoWidth;
                    tempCanvas.height = videoSource.videoHeight;
                    const ctx = tempCanvas.getContext('2d');
                    ctx.drawImage(videoSource, 0, 0);
                    
                    const dataURL = tempCanvas.toDataURL('image/jpeg', 0.8);
                    state.imageSrc = dataURL;
                    
                    addImageToCanvas(dataURL);
                    resolve(dataURL);
                };
            });
        }

        async function generateThumbnails() {
            const numThumbnails = 10;
            const duration = state.videoDuration;
            const interval = duration / numThumbnails;
            
            timelineThumbnails.innerHTML = '';
            
            const thumbWidth = timelineWrapper.clientWidth / numThumbnails;
            const thumbHeight = 50;
            
            for (let i = 0; i < numThumbnails; i++) {
                const time = i * interval;
                const thumbCanvas = document.createElement('canvas');
                thumbCanvas.width = thumbWidth;
                thumbCanvas.height = thumbHeight;
                thumbCanvas.style.width = thumbWidth + 'px';
                
                timelineThumbnails.appendChild(thumbCanvas);
                
                // Capture thumbnail asynchronously
                await captureThumbnail(time, thumbCanvas);
            }
        }

        function captureThumbnail(time, thumbCanvas) {
            return new Promise((resolve) => {
                videoSource.currentTime = time;
                videoSource.onseeked = () => {
                    const ctx = thumbCanvas.getContext('2d');
                    const aspectRatio = videoSource.videoWidth / videoSource.videoHeight;
                    const drawHeight = thumbCanvas.height;
                    const drawWidth = drawHeight * aspectRatio;
                    const offsetX = (thumbCanvas.width - drawWidth) / 2;
                    
                    ctx.fillStyle = '#000';
                    ctx.fillRect(0, 0, thumbCanvas.width, thumbCanvas.height);
                    ctx.drawImage(videoSource, offsetX, 0, drawWidth, drawHeight);
                    resolve();
                };
            });
        }

        // ============================================
        // TIMELINE CONTROLS
        // ============================================
        function updateTimelineUI() {
            const duration = state.videoDuration;
            const startPercent = (state.trimStart / duration) * 100;
            const endPercent = (state.trimEnd / duration) * 100;
            
            timelineSelection.style.left = startPercent + '%';
            timelineSelection.style.width = (endPercent - startPercent) + '%';
            
            timeStartEl.textContent = formatTime(state.trimStart);
            timeEndEl.textContent = formatTime(state.trimEnd);
            trimDurationEl.textContent = formatTime(state.trimEnd - state.trimStart);
        }

        function formatTime(seconds) {
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return `${mins}:${secs.toString().padStart(2, '0')}`;
        }

        function setupTimelineInteraction() {
            let dragging = null;
            let startX = 0;
            let startValue = 0;
            let lastPreviewTime = 0;
            const PREVIEW_THROTTLE = 100; // ms between preview updates
            
            const getTimeFromX = (x) => {
                const rect = timelineWrapper.getBoundingClientRect();
                const percent = Math.max(0, Math.min(1, (x - rect.left) / rect.width));
                return percent * state.videoDuration;
            };
            
            // Throttled preview update
            const updatePreview = (time) => {
                const now = Date.now();
                if (now - lastPreviewTime > PREVIEW_THROTTLE) {
                    lastPreviewTime = now;
                    captureVideoFrame(time);
                }
            };
            
            handleStart.addEventListener('mousedown', (e) => {
                dragging = 'start';
                startX = e.clientX;
                startValue = state.trimStart;
                e.preventDefault();
            });
            
            handleEnd.addEventListener('mousedown', (e) => {
                dragging = 'end';
                startX = e.clientX;
                startValue = state.trimEnd;
                e.preventDefault();
            });
            
            // Also allow dragging the selection area
            timelineSelection.addEventListener('mousedown', (e) => {
                if (e.target === timelineSelection) {
                    dragging = 'selection';
                    startX = e.clientX;
                    startValue = state.trimStart;
                    e.preventDefault();
                }
            });
            
            document.addEventListener('mousemove', (e) => {
                if (!dragging) return;
                
                const time = getTimeFromX(e.clientX);
                const duration = state.videoDuration;
                const trimLength = state.trimEnd - state.trimStart;
                
                if (dragging === 'start') {
                    state.trimStart = Math.max(0, Math.min(time, state.trimEnd - 0.5));
                    // Live preview: show first frame of trimmed section
                    updatePreview(state.trimStart);
                } else if (dragging === 'end') {
                    state.trimEnd = Math.min(duration, Math.max(time, state.trimStart + 0.5));
                    // Live preview: show last frame of trimmed section
                    updatePreview(state.trimEnd);
                } else if (dragging === 'selection') {
                    const delta = time - getTimeFromX(startX);
                    let newStart = startValue + delta;
                    let newEnd = newStart + trimLength;
                    
                    if (newStart < 0) {
                        newStart = 0;
                        newEnd = trimLength;
                    }
                    if (newEnd > duration) {
                        newEnd = duration;
                        newStart = duration - trimLength;
                    }
                    
                    state.trimStart = newStart;
                    state.trimEnd = newEnd;
                }
                
                updateTimelineUI();
            });
            
            document.addEventListener('mouseup', async () => {
                if (dragging) {
                    const wasDragging = dragging;
                    dragging = null;
                    // Final preview: show the appropriate frame based on what was dragged
                    if (wasDragging === 'start') {
                        await captureVideoFrame(state.trimStart);
                    } else if (wasDragging === 'end') {
                        await captureVideoFrame(state.trimEnd);
                    } else {
                        await captureVideoFrame(state.trimStart);
                    }
                }
            });
            
            // Click on timeline to seek
            timelineWrapper.addEventListener('click', async (e) => {
                if (e.target === handleStart || e.target === handleEnd) return;
                
                const time = getTimeFromX(e.clientX);
                videoSource.currentTime = time;
                updatePlayhead(time);
                await captureVideoFrame(time);
            });
            
            // Play button
            btnPlay.addEventListener('click', () => {
                if (state.isPlaying) {
                    pauseVideo();
                } else {
                    playVideo();
                }
            });
            
            // Preview button - play trimmed section
            btnPreview.addEventListener('click', () => {
                previewTrimmedSection();
            });
        }

        function updatePlayhead(time) {
            const percent = (time / state.videoDuration) * 100;
            timelinePlayhead.style.left = percent + '%';
        }

        function playVideo() {
            state.isPlaying = true;
            btnPlay.textContent = '⏸️ Pause';
            btnPlay.classList.add('active');
            
            videoSource.currentTime = state.trimStart;
            videoSource.play();
            
            const updateFrame = () => {
                if (!state.isPlaying) return;
                
                const currentTime = videoSource.currentTime;
                updatePlayhead(currentTime);
                
                // Update canvas preview
                if (imageObj) {
                    const tempCanvas = document.createElement('canvas');
                    tempCanvas.width = videoSource.videoWidth;
                    tempCanvas.height = videoSource.videoHeight;
                    const ctx = tempCanvas.getContext('2d');
                    ctx.drawImage(videoSource, 0, 0);
                    
                    imageObj.setSrc(tempCanvas.toDataURL('image/jpeg', 0.8), () => {
                        canvas.renderAll();
                    });
                }
                
                if (currentTime >= state.trimEnd) {
                    pauseVideo();
                    return;
                }
                
                requestAnimationFrame(updateFrame);
            };
            
            requestAnimationFrame(updateFrame);
        }

        function pauseVideo() {
            state.isPlaying = false;
            btnPlay.textContent = '▶️ Play';
            btnPlay.classList.remove('active');
            videoSource.pause();
        }

        async function previewTrimmedSection() {
            await captureVideoFrame(state.trimStart);
            playVideo();
        }

        /**
         * Le fichier choisi n'est pas décodable comme image : on retire ce
         * qui restait sur le plan de travail, on REVERROUILLE les actions de
         * sortie (rien de bon ne peut en sortir) et on le dit à l'écran.
         */
        function rejectUnusableImage() {
            if (imageObj) {
                canvas.remove(imageObj);
                imageObj = null;
            }
            state.imageSrc = null;
            canvas.requestRenderAll();
            updateImageEditReadouts();

            // La vidéo ne passe ici que par une capture de frame déjà rendue
            // par un canvas : on ne touche pas à son bouton d'export.
            if (state.mediaType !== 'video') {
                if (exportBtn) exportBtn.disabled = true;
                if (scheduleBtn) scheduleBtn.disabled = true;
                if (saveMemeBtn) saveMemeBtn.disabled = true;
            }

            note('Ce fichier n’est pas une image exploitable : rien n’a pu être décodé. Choisis un PNG, un JPEG ou un WebP valide.', 'error');
        }

        function addImageToCanvas(src) {
            // Fabric 5 passe `isError` en second argument et rend malgré tout
            // un objet Image — de dimensions 0×0. Sans ce contrôle, un fichier
            // qui n'est pas une image (extension trompeuse, média tronqué)
            // produisait un plan de travail VIDE, sans un mot à l'écran, puis
            // un export « réussi » ne contenant que le gabarit. L'échec doit
            // se voir : c'est le défaut que l'audit reproche déjà à FFmpeg.
            fabric.Image.fromURL(src, (img, isError) => {
                if (isError || !img || !img.width || !img.height) {
                    rejectUnusableImage();
                    return;
                }

                if (imageObj) {
                    canvas.remove(imageObj);
                }

                const template = TEMPLATES[state.currentTemplate];
                const frame = template.frame;
                const offset = CANVAS_PADDING;

                // LOT C — dimensions naturelles mémorisées AVANT tout
                // recadrage : width/height deviennent ensuite la fenêtre
                // de recadrage, plus la taille de la source.
                img._natW = img.width;
                img._natH = img.height;
                applyCropToImage(img, state.cropRatio);

                // Calculate scale to cover the frame
                // Une rotation d'un quart de tour échange largeur et hauteur :
                // sans ça l'image cesse de couvrir le cadre après rotation.
                const quarterTurn = (state.rotation % 180) !== 0;
                const scaleX = frame.width / (quarterTurn ? img.height : img.width);
                const scaleY = frame.height / (quarterTurn ? img.width : img.height);
                const baseScale = Math.max(scaleX, scaleY);
                const finalScale = baseScale * (state.imageScale / 100);

                // Center the image in the frame (with offset)
                const centerX = frame.x + frame.width / 2 + offset;
                const centerY = frame.y + frame.height / 2 + offset;

                img.set({
                    left: centerX + state.imageOffsetX,
                    top: centerY + state.imageOffsetY,
                    originX: 'center',
                    originY: 'center',
                    scaleX: finalScale,
                    scaleY: finalScale,
                    // LOT C — rotation / retournement conservés d'un
                    // changement de format à l'autre.
                    angle: state.rotation,
                    flipX: state.flipX,
                    flipY: state.flipY,
                    hasControls: true,
                    hasBorders: true,
                    cornerSize: 18,
                    // Lock rotation, only allow scale and move
                    lockRotation: true,
                    // Apply the fixed clip path
                    clipPath: clipRect,
                    // Visual styling
                    strokeWidth: 0,
                    borderColor: '#ef4444',
                    borderDashArray: [5, 5],
                    hoverCursor: 'move',
                    moveCursor: 'move'
                });

                // Store base scale for slider calculations
                img._baseScale = baseScale;

                imageObj = img;
                canvas.add(imageObj);
                
                // Reorder layers - templateBg at very back, then frameRect, then image
                canvas.sendToBack(imageObj);
                canvas.sendToBack(frameBorder);
                canvas.sendToBack(frameRect);
                canvas.sendToBack(templateBg);
                canvas.bringToFront(textBox);
                canvas.bringToFront(watermark);
                
                if (overlayTextObj) {
                    canvas.bringToFront(overlayTextObj);
                }

                // LOT C — réglages (luminosité / contraste / saturation)
                // réappliqués : ils survivent au changement de format.
                applyImageFilters();
                updateImageEditReadouts();

                // Track image movement
                imageObj.on('moving', function() {
                    const template = TEMPLATES[state.currentTemplate];
                    const frame = template.frame;
                    const offset = CANVAS_PADDING;
                    const centerX = frame.x + frame.width / 2 + offset;
                    const centerY = frame.y + frame.height / 2 + offset;
                    
                    state.imageOffsetX = this.left - centerX;
                    state.imageOffsetY = this.top - centerY;
                });

                // Track image scaling
                imageObj.on('scaling', function() {
                    const currentScale = this.scaleX;
                    const baseScale = this._baseScale;
                    const percentage = Math.round((currentScale / baseScale) * 100);
                    
                    state.imageScale = percentage;
                    imageScaleSlider.value = Math.min(200, Math.max(50, percentage));
                    imageScaleValue.textContent = percentage + '%';
                });

                canvas.renderAll();
            }, { crossOrigin: 'anonymous' });
        }

        function updateImageScale(percentage) {
            state.imageScale = percentage;
            imageScaleValue.textContent = percentage + '%';
            
            if (imageObj && imageObj._baseScale) {
                const newScale = imageObj._baseScale * (percentage / 100);
                imageObj.set({
                    scaleX: newScale,
                    scaleY: newScale
                });
                canvas.renderAll();
            }
        }

        function updateFrameHeight(percentage) {
            state.frameHeightPercent = percentage;
            frameHeightValue.textContent = percentage + '%';
            
            const template = TEMPLATES[state.currentTemplate];
            const frame = template.frame;
            const offset = CANVAS_PADDING;
            
            // Calculer la nouvelle hauteur effective
            const effectiveFrameHeight = Math.round(frame.height * (percentage / 100));
            
            // Calculer le centre original du cadre
            const originalCenterY = frame.y + frame.height / 2;
            
            // Calculer la nouvelle position Y pour garder le cadre centré
            const newFrameY = originalCenterY - effectiveFrameHeight / 2;
            
            // Mettre à jour les dimensions ET la position du cadre
            if (clipRect) {
                clipRect.set({ 
                    top: newFrameY + offset,
                    height: effectiveFrameHeight 
                });
            }
            if (frameRect) {
                frameRect.set({ 
                    top: newFrameY + offset,
                    height: effectiveFrameHeight 
                });
            }
            if (frameBorder) {
                frameBorder.set({ 
                    top: newFrameY + offset,
                    height: effectiveFrameHeight 
                });
            }
            
            canvas.renderAll();
        }

        function updateUploadZone() {
            if (state.imageSrc || state.videoFile) {
                const sizeKB = Math.round(state.imageSize / 1024);
                const isVideo = state.mediaType === 'video';
                const icon = isVideo ? '🎬' : '🖼️';
                const previewSrc = state.imageSrc || ''; // For video, this is the first frame
                
                uploadZone.classList.add('has-file');
                uploadZone.innerHTML = `
                    <div class="file-preview">
                        ${previewSrc ? `<img src="${previewSrc}" alt="preview">` : `<div style="width:56px;height:56px;background:#333;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:24px;">${icon}</div>`}
                        <div class="file-info">
                            <div class="file-name">${state.imageName}</div>
                            <div class="file-size">${sizeKB > 1024 ? (sizeKB/1024).toFixed(1) + ' MB' : sizeKB + ' KB'}${isVideo ? ' • ' + formatTime(state.videoDuration) : ''}</div>
                        </div>
                        <button class="remove-file" onclick="removeImage(event)">✕</button>
                    </div>
                `;
            } else {
                uploadZone.classList.remove('has-file');
                uploadZone.innerHTML = `
                    <div class="upload-icon">🖼️</div>
                    <div class="upload-text"><strong>Clique</strong> ou glisse une image/vidéo</div>
                `;
            }
        }

        window.removeImage = function(e) {
            e.stopPropagation();
            
            // Reset all media state
            state.mediaType = null;
            state.imageSrc = null;
            state.imageName = '';
            state.imageSize = 0;
            state.imageScale = 100;
            state.imageOffsetX = 0;
            state.imageOffsetY = 0;
            
            // Reset video state
            state.videoFile = null;
            state.videoDuration = 0;
            state.trimStart = 0;
            state.trimEnd = 0;
            state.isPlaying = false;
            
            // Clean up video source
            if (videoSource.src) {
                URL.revokeObjectURL(videoSource.src);
                videoSource.src = '';
            }
            
            fileInput.value = '';
            
            if (imageObj) {
                canvas.remove(imageObj);
                imageObj = null;
            }
            
            // Hide all media-related UI
            imageScaleSection.style.display = 'none';
            selectImageBtn.style.display = 'none';
            timelineContainer.style.display = 'none';
            mediaTypeBadge.style.display = 'none';
            
            updateUploadZone();
            exportBtn.disabled = true;
            exportBtn.textContent = '📥 Télécharger le meme';
            scheduleBtn.disabled = true;
            if (saveMemeBtn) saveMemeBtn.disabled = true;
            canvas.renderAll();
        };

        // ============================================
        // TEXT HANDLING
        // ============================================
        function updateText(text) {
            state.text = text;
            if (textBox) {
                textBox.set({
                    text: text || 'Tape ton texte...',
                    fill: '#000000'
                });
                canvas.renderAll();
            }
        }

        function updateTextSize(size) {
            state.textSize = size;
            textSizeValue.textContent = size + 'px';
            if (textBox) {
                textBox.set({ fontSize: parseInt(size) });
                canvas.renderAll();
            }
        }

        function updateLineHeight(value) {
            // value est en pourcentage (80-200), on le convertit en ratio (0.8-2.0)
            const ratio = value / 100;
            state.lineHeight = ratio;
            lineHeightValue.textContent = ratio.toFixed(1);
            if (textBox) {
                textBox.set({ lineHeight: ratio });
                canvas.renderAll();
            }
        }

        // ============================================
        // OVERLAY TEXT
        // ============================================
        function toggleOverlay() {
            state.showOverlay = !state.showOverlay;
            overlaySwitch.classList.toggle('active', state.showOverlay);
            overlayTextInput.style.display = state.showOverlay ? 'block' : 'none';
            
            if (state.showOverlay && state.overlayText) {
                addOverlayText();
            } else if (overlayTextObj) {
                canvas.remove(overlayTextObj);
                overlayTextObj = null;
            }
            canvas.renderAll();
        }

        function addOverlayText() {
            if (overlayTextObj) {
                canvas.remove(overlayTextObj);
            }

            const template = TEMPLATES[state.currentTemplate];
            const frame = template.frame;
            const offset = CANVAS_PADDING;

            overlayTextObj = new fabric.Text(state.overlayText.toUpperCase(), {
                left: frame.x + frame.width / 2 + offset,
                top: frame.y + frame.height - 60 + offset,
                fontSize: template.width * 0.055,
                fontFamily: 'Impact, Haettenschweiler, sans-serif',
                fontWeight: '900',
                fill: '#ffffff',
                stroke: '#000000',
                strokeWidth: template.width * 0.006,
                textAlign: 'center',
                originX: 'center',
                originY: 'bottom',
                hasControls: true,
                cornerSize: 16,
                hoverCursor: 'move',
                moveCursor: 'move'
            });

            canvas.add(overlayTextObj);
            canvas.bringToFront(overlayTextObj);
            canvas.bringToFront(watermark);
            canvas.renderAll();
        }

        function updateOverlayText(text) {
            state.overlayText = text;
            if (state.showOverlay && text) {
                addOverlayText();
            } else if (overlayTextObj && !text) {
                canvas.remove(overlayTextObj);
                overlayTextObj = null;
                canvas.renderAll();
            }
        }

        function updateWatermarkOpacity(value) {
            state.watermarkOpacity = value;
            watermarkOpacityValue.textContent = value + '%';
            
            if (watermark) {
                watermark.set('opacity', value / 100);
                canvas.renderAll();
            }
        }

        // ============================================
        // GOOGLE DRIVE PICKER
        // ============================================
        // Google API Config - Replace with your credentials
        const GOOGLE_API_KEY = localStorage.getItem('samourais_google_api_key') || '';
        const GOOGLE_CLIENT_ID = localStorage.getItem('samourais_google_client_id') || '';
        const GOOGLE_APP_ID = '';
        let pickerApiLoaded = false;
        let oauthToken = null;

        function openGoogleDrivePicker() {
            if (!GOOGLE_API_KEY || !GOOGLE_CLIENT_ID) {
                showDriveConfigModal();
                return;
            }
            
            // Show loading
            driveConnect.style.display = 'none';
            driveLoading.style.display = 'flex';
            
            // Load the Google API
            gapi.load('auth2', () => {
                gapi.load('picker', () => {
                    pickerApiLoaded = true;
                    authenticateAndShowPicker();
                });
            });
        }

        function authenticateAndShowPicker() {
            gapi.auth2.authorize({
                client_id: GOOGLE_CLIENT_ID,
                scope: 'https://www.googleapis.com/auth/drive.readonly',
                immediate: false
            }, (authResult) => {
                if (authResult && !authResult.error) {
                    oauthToken = authResult.access_token;
                    createPicker();
                } else {
                    driveLoading.style.display = 'none';
                    driveConnect.style.display = 'block';
                    console.error('Auth error:', authResult?.error);
                    note('Connexion à Google Drive impossible. Vérifie les identifiants dans les Réglages, puis réessaie.', 'error');
                }
            });
        }

        function createPicker() {
            if (pickerApiLoaded && oauthToken) {
                const view = new google.picker.DocsView()
                    .setIncludeFolders(true)
                    .setMimeTypes('image/png,image/jpeg,image/gif,image/webp,video/mp4,video/quicktime,video/webm')
                    .setSelectFolderEnabled(false);
                
                const picker = new google.picker.PickerBuilder()
                    .setAppId(GOOGLE_APP_ID)
                    .setOAuthToken(oauthToken)
                    .addView(view)
                    .addView(new google.picker.DocsView().setIncludeFolders(true).setSelectFolderEnabled(true))
                    .setDeveloperKey(GOOGLE_API_KEY)
                    .setCallback(pickerCallback)
                    .setTitle('Sélectionne une image ou vidéo')
                    .setLocale('fr')
                    .build();
                
                picker.setVisible(true);
                
                // Hide loading
                driveLoading.style.display = 'none';
                driveConnect.style.display = 'block';
            }
        }

        function pickerCallback(data) {
            if (data.action === google.picker.Action.PICKED) {
                const file = data.docs[0];
                loadFromDrive(file);
            }
        }

        async function loadFromDrive(file) {
            driveConnect.style.display = 'none';
            driveLoading.style.display = 'flex';
            driveLoading.querySelector('span').textContent = 'Chargement de ' + file.name + '...';
            
            try {
                // Fetch the file content
                const response = await fetch(`https://www.googleapis.com/drive/v3/files/${file.id}?alt=media`, {
                    headers: {
                        'Authorization': 'Bearer ' + oauthToken
                    }
                });
                
                const blob = await response.blob();
                const blobFile = new File([blob], file.name, { type: file.mimeType });
                
                // Switch back to local tab to show preview
                importTabs.forEach(t => t.classList.remove('active'));
                document.querySelector('.import-tab[data-source="local"]').classList.add('active');
                uploadZone.style.display = 'block';
                driveZone.style.display = 'none';
                
                // Load the file
                loadMedia(blobFile);
                
            } catch (error) {
                console.error('Error loading from Drive:', error);
                note('Le fichier n\u2019a pas pu être chargé depuis Google Drive. Réessaie ; si l\u2019erreur persiste, reconnecte le compte dans les Réglages.', 'error');
                driveLoading.style.display = 'none';
                driveConnect.style.display = 'block';
            }
        }

        function showDriveConfigModal() {
            const modal = document.createElement('div');
            modal.id = 'drive-config-modal';
            modal.innerHTML = `
                <div style="position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center; z-index: 10000;">
                    <div style="background: #1a1a1a; border-radius: 16px; padding: 24px; max-width: 450px; width: 90%;">
                        <h3 style="margin-bottom: 16px; font-size: 18px;">⚙️ Configuration Google Drive</h3>
                        <p style="color: #888; font-size: 13px; margin-bottom: 16px;">
                            Pour utiliser Google Drive, tu dois d'abord configurer tes identifiants Google Cloud.
                            <a href="https://console.cloud.google.com/" target="_blank" style="color: #ef4444;">Créer un projet</a>
                        </p>
                        <div style="margin-bottom: 12px;">
                            <label style="display: block; font-size: 12px; color: #888; margin-bottom: 6px;">API Key</label>
                            <input type="text" id="drive-api-key" placeholder="AIza..." 
                                   style="width: 100%; padding: 12px; background: #111; border: 2px solid #333; border-radius: 8px; color: #fff; font-size: 14px;">
                        </div>
                        <div style="margin-bottom: 16px;">
                            <label style="display: block; font-size: 12px; color: #888; margin-bottom: 6px;">Client ID</label>
                            <input type="text" id="drive-client-id" placeholder="xxxxx.apps.googleusercontent.com" 
                                   style="width: 100%; padding: 12px; background: #111; border: 2px solid #333; border-radius: 8px; color: #fff; font-size: 14px;">
                        </div>
                        <div style="display: flex; gap: 12px;">
                            <button onclick="document.getElementById('drive-config-modal').remove()" 
                                    style="flex: 1; padding: 12px; background: #333; border: none; border-radius: 8px; color: #fff; cursor: pointer;">
                                Annuler
                            </button>
                            <button onclick="saveDriveConfig()" 
                                    style="flex: 1; padding: 12px; background: #1a73e8; border: none; border-radius: 8px; color: #fff; cursor: pointer; font-weight: 600;">
                                Sauvegarder
                            </button>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }

        window.saveDriveConfig = function() {
            const apiKey = document.getElementById('drive-api-key').value.trim();
            const clientId = document.getElementById('drive-client-id').value.trim();
            
            if (apiKey && clientId) {
                localStorage.setItem('samourais_google_api_key', apiKey);
                localStorage.setItem('samourais_google_client_id', clientId);
                document.getElementById('drive-config-modal').remove();
                
                // Reload to apply
                location.reload();
            } else {
                note('Les deux champs sont obligatoires.', 'warning');
            }
        };

        // ============================================
        // RETOUCHE IMAGE (LOT C)
        // --------------------------------------------
        // Recadrage au ratio, rotation, retournement et réglages de base.
        // Tout passe par Fabric : cropX/cropY/width/height pour le
        // recadrage (le fichier source n'est jamais réécrit), `angle`
        // pour la rotation, flipX/flipY pour le miroir, et les filtres
        // intégrés Brightness / Contrast / Saturation pour les réglages.
        //
        // Conséquence voulue : ZÉRO aller-retour serveur. Pas d'upload,
        // pas de fichier temporaire à nettoyer, pas de worker bloqué, et
        // aucun échec qui pourrait disparaître sans que l'utilisateur le
        // voie — le seul échec possible (filtre WebGL) est capturé et
        // remonté par `note()`.
        // ============================================

        // Ratios proposés, en largeur/hauteur.
        const CROP_RATIOS = {
            '1': 1, '0.8': 0.8, '0.5625': 0.5625, '1.7777778': 16 / 9
        };

        /**
         * Recadre l'objet Fabric au ratio demandé, centré sur la source.
         * `ratio` null remet l'image entière. Les dimensions naturelles
         * sont mémorisées sur l'objet (_natW/_natH) au premier ajout :
         * width/height sont ensuite la FENÊTRE de recadrage, pas la source.
         */
        function applyCropToImage(img, ratio) {
            const natW = img._natW || img.width;
            const natH = img._natH || img.height;

            if (!ratio || !isFinite(ratio) || ratio <= 0) {
                img.set({ cropX: 0, cropY: 0, width: natW, height: natH });
                return;
            }

            let cw, ch;
            if (natW / natH > ratio) {
                ch = natH;
                cw = Math.round(natH * ratio);
            } else {
                cw = natW;
                ch = Math.round(natW / ratio);
            }
            cw = Math.max(1, Math.min(natW, cw));
            ch = Math.max(1, Math.min(natH, ch));

            img.set({
                cropX: Math.round((natW - cw) / 2),
                cropY: Math.round((natH - ch) / 2),
                width: cw,
                height: ch
            });
        }

        /**
         * Recalcule l'échelle de couverture du cadre et repositionne
         * l'image après un recadrage ou une rotation. Une rotation de
         * 90°/270° échange largeur et hauteur : sans ça l'image cesse de
         * couvrir le cadre et laisse apparaître le fond gris.
         */
        function reapplyImageTransforms() {
            if (!imageObj) return;

            const template = TEMPLATES[state.currentTemplate];
            const frame = template.frame;
            const offset = CANVAS_PADDING;

            applyCropToImage(imageObj, state.cropRatio);

            const quarterTurn = (state.rotation % 180) !== 0;
            const srcW = quarterTurn ? imageObj.height : imageObj.width;
            const srcH = quarterTurn ? imageObj.width : imageObj.height;

            const baseScale = Math.max(frame.width / srcW, frame.height / srcH);
            imageObj._baseScale = baseScale;

            const finalScale = baseScale * (state.imageScale / 100);
            const centerX = frame.x + frame.width / 2 + offset;
            const centerY = frame.y + frame.height / 2 + offset;

            imageObj.set({
                angle: state.rotation,
                flipX: state.flipX,
                flipY: state.flipY,
                scaleX: finalScale,
                scaleY: finalScale,
                left: centerX + state.imageOffsetX,
                top: centerY + state.imageOffsetY
            });
            imageObj.setCoords();
            canvas.requestRenderAll();
            updateImageEditReadouts();
        }

        // Les filtres sont recalculés sur toute l'image : sur un slider
        // qui émet à chaque pixel de course, on ne garde que la dernière
        // valeur de chaque frame.
        let filterFrame = null;

        function applyImageFiltersNow() {
            filterFrame = null;
            if (!imageObj) return;

            const filters = [];
            if (state.brightness) {
                filters.push(new fabric.Image.filters.Brightness({
                    brightness: state.brightness / 100
                }));
            }
            if (state.contrast) {
                filters.push(new fabric.Image.filters.Contrast({
                    contrast: state.contrast / 100
                }));
            }
            if (state.saturation) {
                filters.push(new fabric.Image.filters.Saturation({
                    saturation: state.saturation / 100
                }));
            }

            imageObj.filters = filters;
            try {
                imageObj.applyFilters();
            } catch (err) {
                // Un échec de filtre est VISIBLE : on revient à l'image
                // nue et on le dit, au lieu de laisser un canvas figé
                // sans explication.
                console.error('[editor] application des filtres impossible', err);
                note('Les réglages n’ont pas pu être appliqués à cette image. L’image d’origine est conservée.', 'error');
                imageObj.filters = [];
                try { imageObj.applyFilters(); } catch (e) { /* déjà signalé */ }
            }
            canvas.requestRenderAll();
        }

        function applyImageFilters() {
            if (!imageObj) return;
            if (filterFrame) cancelAnimationFrame(filterFrame);
            filterFrame = requestAnimationFrame(applyImageFiltersNow);
        }

        /**
         * Applique SANS ATTENDRE les réglages encore en file d'attente.
         *
         * Indispensable avant tout rendu : requestAnimationFrame ne
         * garantit rien sur une frame donnée (et n'est même pas servi
         * quand l'onglet est en arrière-plan). Sans ce vidage, une image
         * exportée juste après un mouvement de slider partait SANS le
         * réglage que l'utilisateur venait de voir à l'écran — un écart
         * silencieux entre l'aperçu et le fichier produit.
         */
        function flushImageFilters() {
            if (filterFrame === null) return;
            cancelAnimationFrame(filterFrame);
            applyImageFiltersNow();
        }

        /** Dimensions annoncées : source, fenêtre de recadrage, rotation. */
        function updateImageEditReadouts() {
            if (!cropReadout || !orientReadout) return;

            if (!imageObj) {
                cropReadout.textContent = 'Source : —';
                orientReadout.innerHTML = 'Rotation : <strong>0°</strong>';
                return;
            }

            const natW = imageObj._natW || imageObj.width;
            const natH = imageObj._natH || imageObj.height;
            const cw = Math.round(imageObj.width);
            const ch = Math.round(imageObj.height);

            cropReadout.textContent = (cw === natW && ch === natH)
                ? `Source : ${natW}×${natH} px — image entière`
                : `Source : ${natW}×${natH} px — recadrée à ${cw}×${ch} px`;

            const mirrors = [];
            if (state.flipX) mirrors.push('miroir horizontal');
            if (state.flipY) mirrors.push('miroir vertical');
            orientReadout.innerHTML = `Rotation : <strong>${state.rotation}°</strong>`
                + (mirrors.length ? ' — ' + mirrors.join(', ') : '');
        }

        /** Remet les boutons/sliders de retouche en accord avec `state`. */
        function syncImageEditControls() {
            if (cropGroup) {
                const key = state.cropRatio === null ? 'free' : String(state.cropRatio);
                cropGroup.querySelectorAll('.seg__btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.ratio === key);
                });
            }
            if (flipHBtn) flipHBtn.setAttribute('aria-pressed', String(state.flipX));
            if (flipVBtn) flipVBtn.setAttribute('aria-pressed', String(state.flipY));
            if (adjBrightness) { adjBrightness.value = state.brightness; adjBrightnessValue.textContent = state.brightness; }
            if (adjContrast) { adjContrast.value = state.contrast; adjContrastValue.textContent = state.contrast; }
            if (adjSaturation) { adjSaturation.value = state.saturation; adjSaturationValue.textContent = state.saturation; }
            updateImageEditReadouts();
        }

        /** Remet la retouche à zéro (sans recharger l'image). */
        function resetImageEdits(silent) {
            Object.assign(state, IMAGE_EDIT_DEFAULTS);
            syncImageEditControls();
            if (imageObj) {
                reapplyImageTransforms();
                applyImageFilters();
            }
            if (!silent) note('Retouche annulée : recadrage, rotation et réglages remis à zéro.', 'success');
        }

        // ============================================
        // FICHIER DE SORTIE (LOT C)
        // ============================================

        /** Dimensions réelles du fichier produit, multiplicateur compris. */
        function exportPixelSize() {
            const template = TEMPLATES[state.currentTemplate];
            return {
                width: Math.round(template.width * state.exportScale),
                height: Math.round(template.height * state.exportScale)
            };
        }

        function updateExportReadout() {
            if (!exportDims) return;
            const size = exportPixelSize();
            exportDims.textContent = `${size.width}×${size.height} px`;
            if (exportFormatLabel) {
                exportFormatLabel.textContent = state.exportFormat === 'jpeg'
                    ? `JPEG, qualité ${state.exportQuality}%`
                    : 'PNG';
            }
            if (qualityCtl) {
                qualityCtl.style.display = state.exportFormat === 'jpeg' ? 'block' : 'none';
            }
        }

        /**
         * Rend le plan de travail en image, hors marge de manipulation.
         * Zoom remis à 1, repères de cadre masqués, sélection annulée —
         * puis tout est restauré, y compris si le rendu échoue.
         */
        function renderCanvasToDataURL(format, quality, multiplier) {
            // Ce que l'utilisateur voit doit être ce qui sort du canvas :
            // on vide d'abord les réglages en attente.
            flushImageFilters();

            const template = TEMPLATES[state.currentTemplate];
            const originalZoom = canvas.getZoom();

            frameBorder.set({ visible: false });
            frameRect.set({ visible: false });
            canvas.discardActiveObject();
            canvas.setZoom(1);
            canvas.setWidth(template.width + (CANVAS_PADDING * 2));
            canvas.setHeight(template.height + (CANVAS_PADDING * 2));
            canvas.renderAll();

            try {
                return canvas.toDataURL({
                    format: format,
                    quality: quality,
                    multiplier: multiplier || 1,
                    left: CANVAS_PADDING,
                    top: CANVAS_PADDING,
                    width: template.width,
                    height: template.height
                });
            } finally {
                canvas.setZoom(originalZoom);
                updateCanvasSize();
                frameBorder.set({ visible: true });
                frameRect.set({ visible: true });
                canvas.renderAll();
            }
        }

        /** Poids d'une data URL base64, en octets. */
        function dataURLBytes(dataURL) {
            const base64 = dataURL.slice(dataURL.indexOf(',') + 1);
            const padding = base64.endsWith('==') ? 2 : (base64.endsWith('=') ? 1 : 0);
            return Math.max(0, Math.floor(base64.length * 3 / 4) - padding);
        }

        function formatBytes(bytes) {
            if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' Mo';
            return Math.max(1, Math.round(bytes / 1024)) + ' Ko';
        }

        /** Révèle/masque les blocs qui n'ont de sens que pour une image. */
        function updateMediaToolsVisibility() {
            const isImage = state.mediaType === 'image';
            if (imageTools) imageTools.style.display = isImage ? 'flex' : 'none';
            // L'export vidéo est MP4 H.264 : ni format image, ni qualité,
            // ni multiplicateur ne s'y appliquent. On ne montre pas des
            // réglages qui n'auraient aucun effet.
            if (outputTools) outputTools.style.display = (state.mediaType === 'video') ? 'none' : 'flex';
            updateExportReadout();
        }

        // ============================================
        // RESET & EXPORT
        // ============================================
        function resetAll() {
            state.imageOffsetX = 0;
            state.imageOffsetY = 0;
            state.imageScale = 100;
            
            imageScaleSlider.value = 100;
            imageScaleValue.textContent = '100%';

            // LOT C — « Réinitialiser » repart d'une composition vierge :
            // la retouche de l'image en fait partie.
            Object.assign(state, IMAGE_EDIT_DEFAULTS);
            syncImageEditControls();

            createElements();

            if (state.imageSrc) {
                addImageToCanvas(state.imageSrc);
            }
            if (state.showOverlay && state.overlayText) {
                addOverlayText();
            }
            updateText(state.text);
            updateTextSize(state.textSize);
        }

        function exportMeme() {
            if (state.mediaType === 'video') {
                exportVideo();
            } else {
                exportImage();
            }
        }

        function schedulePost() {
            // LOT C — même règle que pour l'export : la vignette envoyée au
            // Calendrier doit porter les réglages déjà visibles à l'écran.
            flushImageFilters();

            // Capture current canvas state as image
            frameBorder.set({ visible: false });
            frameRect.set({ visible: false });
            canvas.discardActiveObject();
            canvas.renderAll();

            const template = TEMPLATES[state.currentTemplate];
            
            // Reset zoom temporarily for clean capture
            const originalZoom = canvas.getZoom();
            canvas.setZoom(1);
            canvas.setWidth(template.width + (CANVAS_PADDING * 2));
            canvas.setHeight(template.height + (CANVAS_PADDING * 2));
            canvas.renderAll();
            
            // Capture the canvas
            const dataURL = canvas.toDataURL({
                format: 'jpeg',
                quality: 0.9,
                left: CANVAS_PADDING,
                top: CANVAS_PADDING,
                width: template.width,
                height: template.height
            });

            // Restore zoom and size
            canvas.setZoom(originalZoom);
            updateCanvasSize();
            frameBorder.set({ visible: true });
            frameRect.set({ visible: true });
            canvas.renderAll();

            // Post directly to calendar API
            const postData = {
                title: 'Meme — ' + state.currentTemplate,
                caption: state.text || '',
                media_type: state.mediaType === 'video' ? 'video' : 'image',
                template_format: state.currentTemplate,
                thumbnail: dataURL,  // base64 data URL saved as thumbnail
                status: 'draft',
                platforms: '[]',
            };

            fetch('/api/calendar/posts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(postData)
            })
            .then(r => {
                if (!r.ok) throw new Error('Failed to create post');
                return r.json();
            })
            .then(result => {
                // Redirect to calendar
                window.location.href = '/calendar';
            })
            .catch(err => {
                console.error('Schedule error:', err);
                note('La planification a échoué. Le post est conservé localement : rouvre le Calendrier pour le reprendre.', 'error');
                // Fallback: save to sessionStorage
                sessionStorage.setItem('samourais_pending_post', JSON.stringify({
                    mediaSrc: dataURL,
                    mediaType: state.mediaType === 'video' ? 'video' : 'image',
                    template: state.currentTemplate,
                    caption: state.text || '',
                    timestamp: Date.now()
                }));
                window.location.href = '/calendar';
            });
        }

        function saveMemeToViewer() {
            if (state.mediaType === 'video') {
                note('La sauvegarde vers le Viewer ne prend pas encore les vidéos. Utilise « Télécharger » pour récupérer le fichier.', 'warning');
                return;
            }

            // LOT C — la taille cible s'applique aussi à la copie Viewer.
            // Le format reste PNG : /api/viewer/memes écrit toujours un
            // fichier .png et le sert en image/png — y déposer du JPEG
            // produirait un fichier mal nommé et mal typé.
            const size = exportPixelSize();
            let dataURL;
            try {
                dataURL = renderCanvasToDataURL('png', 1, state.exportScale);
            } catch (err) {
                console.error('[editor] rendu du meme impossible', err);
                note('Le meme n’a pas pu être rendu. Réduis la taille cible et réessaie.', 'error');
                return;
            }

            if (!dataURL || dataURL.length < 100) {
                note('Le meme rendu est vide. Réduis la taille cible et réessaie.', 'error');
                return;
            }

            // Save to backend
            if (saveMemeBtn) {
                saveMemeBtn.disabled = true;
                saveMemeBtn.textContent = '⏳ Sauvegarde...';
            }

            fetch('/api/viewer/memes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    image_data: dataURL,
                    title: 'Meme — ' + state.currentTemplate,
                    caption: state.text || '',
                    template_format: state.currentTemplate,
                    media_type: 'image',
                })
            })
            .then(r => {
                if (!r.ok) throw new Error('Failed to save meme');
                return r.json();
            })
            .then(result => {
                if (saveMemeBtn) {
                    saveMemeBtn.disabled = false;
                    saveMemeBtn.textContent = '✅ Sauvegarde !';
                    setTimeout(() => {
                        saveMemeBtn.textContent = '💾 Sauvegarder dans Viewer';
                    }, 2000);
                }
                // LOT C — dimensions réellement enregistrées, pas un « OK » nu.
                note(`Meme enregistré dans le Viewer : PNG ${size.width}×${size.height} px.`, 'success');
            })
            .catch(err => {
                console.error('Save meme error:', err);
                note('Le meme n\u2019a pas pu être sauvegardé. Réessaie ; si l\u2019erreur persiste, télécharge le fichier pour ne rien perdre.', 'error');
                if (saveMemeBtn) {
                    saveMemeBtn.disabled = false;
                    saveMemeBtn.textContent = '💾 Sauvegarder dans Viewer';
                }
            });
        }

        function exportImage() {
            // LOT C — format (PNG/JPEG), qualité et taille cible viennent
            // du bloc « Fichier de sortie ». Tout est rendu par le canvas :
            // pas d'upload, pas de fichier temporaire, retour immédiat.
            const isJpeg = state.exportFormat === 'jpeg';
            const size = exportPixelSize();

            let dataURL;
            try {
                dataURL = renderCanvasToDataURL(
                    isJpeg ? 'jpeg' : 'png',
                    isJpeg ? state.exportQuality / 100 : 1,
                    state.exportScale
                );
            } catch (err) {
                // Un export raté ne disparaît pas en silence : il se dit.
                console.error('[editor] export image impossible', err);
                note('L’image n’a pas pu être produite. Réduis la taille cible et réessaie.', 'error');
                return;
            }

            if (!dataURL || dataURL.length < 100) {
                note('L’image produite est vide. Réduis la taille cible et réessaie.', 'error');
                return;
            }

            const link = document.createElement('a');
            const timestamp = new Date().toISOString().slice(0, 10);
            link.download = `samourais_meme_${state.currentTemplate}_${size.width}x${size.height}_${timestamp}.${isJpeg ? 'jpg' : 'png'}`;
            link.href = dataURL;
            link.click();

            note(`Image téléchargée : ${size.width}×${size.height} px, ${isJpeg ? 'JPEG ' + state.exportQuality + '%' : 'PNG'}, ${formatBytes(dataURLBytes(dataURL))}.`, 'success');
        }

        async function exportVideo() {
            // Collect all parameters needed for video processing
            const template = TEMPLATES[state.currentTemplate];
            
            // Calculate effective frame height and centered Y position
            const effectiveFrameHeight = Math.round(template.frame.height * (state.frameHeightPercent / 100));
            const originalCenterY = template.frame.y + template.frame.height / 2;
            const effectiveFrameY = originalCenterY - effectiveFrameHeight / 2;
            
            const exportParams = {
                // Template info
                template: state.currentTemplate,
                templateWidth: template.width,
                templateHeight: template.height,
                // Frame info (with adjusted height and Y position for the template PNG)
                frameX: template.frame.x,
                frameY: effectiveFrameY,
                frameWidth: template.frame.width,
                frameHeight: effectiveFrameHeight,
                frameRadius: template.frame.radius,
                // Original frame dimensions for video scaling/positioning (before slider adjustment)
                originalFrameY: template.frame.y,
                originalFrameHeight: template.frame.height,
                // Video trim
                trimStart: state.trimStart,
                trimEnd: state.trimEnd,
                // Media position/scale
                imageScale: state.imageScale,
                imageOffsetX: state.imageOffsetX,
                imageOffsetY: state.imageOffsetY,
                // Text
                text: state.text,
                textSize: state.textSize,
                lineHeight: state.lineHeight,
                textX: textBox ? textBox.left - CANVAS_PADDING : template.textArea.x,
                textY: textBox ? textBox.top - CANVAS_PADDING : template.textArea.y,
                // Overlay
                overlayText: state.showOverlay ? state.overlayText : '',
                // Watermark position and opacity
                watermarkX: watermark ? watermark.left - CANVAS_PADDING : template.watermark.x,
                watermarkY: watermark ? watermark.top - CANVAS_PADDING : template.watermark.y,
                watermarkOpacity: state.watermarkOpacity,
            };

            
            // Show processing modal
            showVideoExportModal(exportParams);
        }

        function showVideoExportModal(params) {
            // Create modal overlay
            const modal = document.createElement('div');
            modal.id = 'export-modal';
            modal.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(0,0,0,0.9);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 1000;
            `;
            
            modal.innerHTML = `
                <div style="background: #1a1a1a; border-radius: 12px; padding: 32px; max-width: 500px; text-align: center;">
                    <div style="font-size: 48px; margin-bottom: 16px;">🎬</div>
                    <h2 style="margin-bottom: 16px; color: #fff;">Export Vidéo</h2>
                    <p style="color: #888; margin-bottom: 24px; line-height: 1.6;">
                        Backend FFmpeg intégré ✅<br>
                        <strong style="color: #ef4444;">Durée: ${formatTime(params.trimEnd - params.trimStart)}</strong>
                    </p>
                    <div style="background: #111; border-radius: 8px; padding: 16px; margin-bottom: 24px; text-align: left;">
                        <div style="font-size: 12px; color: #666; margin-bottom: 8px;">PARAMÈTRES D'EXPORT</div>
                        <div style="font-size: 13px; color: #aaa; font-family: monospace;">
                            Template: ${params.template} (${params.templateWidth}x${params.templateHeight})<br>
                            Trim: ${formatTime(params.trimStart)} → ${formatTime(params.trimEnd)}<br>
                            Scale: ${params.imageScale}%
                        </div>
                    </div>
                    <div style="display: flex; gap: 12px;">
                        <button onclick="document.getElementById('export-modal').remove()"
                                style="flex: 1; padding: 12px; background: #333; border: none; border-radius: 8px; color: #fff; cursor: pointer;">
                            Annuler
                        </button>
                        <button onclick="startVideoProcessing()"
                                style="flex: 1; padding: 12px; background: #ef4444; border: none; border-radius: 8px; color: #fff; cursor: pointer; font-weight: 600;">
                            🚀 Exporter
                        </button>
                    </div>
                </div>
            `;
            
            document.body.appendChild(modal);
            
            // Store params globally for the export button
            window._videoExportParams = params;
        }

        // ============================================
        // BACKEND CONFIG
        // ============================================
        // Backend URL — same origin (Flask serves everything)
        const BACKEND_URL = '';

        // ============================================
        // LOGO WATERMARK CONFIG
        // ============================================
        const LOGO_URL = '/static/samourais_logo_transparent_smooth.png';
        let logoImage = null;
        
        // Preload logo
        function loadLogo() {
            fabric.Image.fromURL(LOGO_URL, (img) => {
                if (img) {
                    logoImage = img;
                    // Refresh watermark if canvas already initialized
                    if (canvas && watermark) {
                        const template = TEMPLATES[state.currentTemplate];
                        const wm = template.watermark;
                        const offset = CANVAS_PADDING;
                        
                        // Remove old text watermark
                        canvas.remove(watermark);
                        
                        // Add logo watermark
                        watermark = new fabric.Image(logoImage.getElement(), {
                            left: wm.x + offset,
                            top: wm.y + offset,
                            originX: 'right',
                            originY: 'bottom',
                            scaleX: 0.15,
                            scaleY: 0.15,
                            opacity: state.watermarkOpacity / 100,
                            selectable: true,
                            hasControls: true,
                            cornerSize: 16,
                            hoverCursor: 'move',
                            moveCursor: 'move'
                        });
                        canvas.add(watermark);
                        canvas.renderAll();
                    }
                }
            }, { crossOrigin: 'anonymous' });
        }

        // Générer le template PNG avec trou transparent pour la vidéo
        async function generateTemplatePNG(params) {
            const template = TEMPLATES[state.currentTemplate];
            const offset = CANVAS_PADDING;
            
            // Récupérer le texte directement depuis le textBox (priorité), puis params, puis state
            // Car l'utilisateur peut taper directement dans le textBox sans passer par l'input
            let textToRender = '';
            if (textBox && textBox.text && textBox.text !== 'Tape ton texte...') {
                textToRender = textBox.text;
            } else if (params.text) {
                textToRender = params.text;
            } else if (state.text) {
                textToRender = state.text;
            }
            
            // Prendre en compte le scale du textBox (si redimensionné manuellement)
            const textBoxScale = textBox ? (textBox.scaleX || 1) : 1;
            const textSizeToUse = (params.textSize || state.textSize || 60) * textBoxScale;
            
            // Position du texte depuis textBox (Fabric.js) ou params
            const textXPos = params.textX !== undefined ? params.textX : (textBox ? textBox.left - offset : template.textArea.x);
            const textYPos = params.textY !== undefined ? params.textY : (textBox ? textBox.top - offset : template.textArea.y);
            
            
            // Attendre que les polices soient chargées
            await document.fonts.ready;
            
            // Créer un canvas temporaire à la taille du template (sans padding)
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = template.width;
            tempCanvas.height = template.height;
            const ctx = tempCanvas.getContext('2d');
            
            // Fond blanc
            ctx.fillStyle = 'white';
            ctx.fillRect(0, 0, template.width, template.height);
            
            // Dessiner le texte principal AVANT de découper le trou
            if (textToRender && textToRender.trim()) {
                ctx.save();
                
                // Utiliser la même police que le textBox Fabric.js (fontWeight 300 = light)
                ctx.font = `300 ${textSizeToUse}px "Inter", Helvetica, Arial, sans-serif`;
                ctx.fillStyle = 'black';
                ctx.textAlign = 'left';
                ctx.textBaseline = 'top';
                
                const lineHeight = textSizeToUse * state.lineHeight;
                
                // Utiliser les lignes réelles du textBox Fabric.js (word wrap inclus)
                if (textBox && textBox._textLines) {
                    let y = textYPos;
                    for (let i = 0; i < textBox._textLines.length; i++) {
                        const line = textBox._textLines[i].join(''); // _textLines est un array d'arrays de caractères
                        ctx.fillText(line, textXPos, y);
                        y += lineHeight;
                    }
                } else {
                    // Fallback: split par \n
                    const lines = textToRender.split('\n');
                    let y = textYPos;
                    for (const line of lines) {
                        ctx.fillText(line, textXPos, y);
                        y += lineHeight;
                    }
                }
                
                ctx.restore();
            }
            
            // Découper le trou pour la vidéo (zone transparente avec coins arrondis)
            ctx.save();
            ctx.globalCompositeOperation = 'destination-out';
            
            // Utiliser les dimensions du frame depuis params (inclut la hauteur personnalisée)
            const frameX = params.frameX;
            const frameY = params.frameY;
            const frameWidth = params.frameWidth;
            const frameHeight = params.frameHeight;
            const radius = params.frameRadius || 0;
            
            // Dessiner un rectangle arrondi transparent
            ctx.beginPath();
            ctx.moveTo(frameX + radius, frameY);
            ctx.lineTo(frameX + frameWidth - radius, frameY);
            ctx.quadraticCurveTo(frameX + frameWidth, frameY, frameX + frameWidth, frameY + radius);
            ctx.lineTo(frameX + frameWidth, frameY + frameHeight - radius);
            ctx.quadraticCurveTo(frameX + frameWidth, frameY + frameHeight, frameX + frameWidth - radius, frameY + frameHeight);
            ctx.lineTo(frameX + radius, frameY + frameHeight);
            ctx.quadraticCurveTo(frameX, frameY + frameHeight, frameX, frameY + frameHeight - radius);
            ctx.lineTo(frameX, frameY + radius);
            ctx.quadraticCurveTo(frameX, frameY, frameX + radius, frameY);
            ctx.closePath();
            ctx.fill();
            
            ctx.restore();
            
            // Le texte est maintenant dessiné AVANT le trou (voir plus haut)
            
            // Dessiner le logo watermark
            if (watermark && logoImage) {
                ctx.save();
                
                // Position du watermark (depuis Fabric.js - originX: right, originY: bottom)
                const wmLeft = watermark.left - offset;
                const wmTop = watermark.top - offset;
                const wmWidth = watermark.width * watermark.scaleX;
                const wmHeight = watermark.height * watermark.scaleY;
                
                // Opacité
                ctx.globalAlpha = state.watermarkOpacity / 100;
                
                // Le watermark est aligné right/bottom dans Fabric, donc:
                // left/top représentent le coin bottom-right
                ctx.drawImage(
                    logoImage.getElement(),
                    wmLeft - wmWidth,
                    wmTop - wmHeight,
                    wmWidth,
                    wmHeight
                );
                
                ctx.restore();
            }
            
            // Convertir en blob PNG
            return new Promise((resolve) => {
                tempCanvas.toBlob((blob) => {
                    resolve(blob);
                }, 'image/png');
            });
        }

        window.startVideoProcessing = async function() {
            const modal = document.getElementById('export-modal');
            const params = window._videoExportParams;
            
            // Backend is always available (same origin)
            let backendUrl = '';

            // Show processing state
            modal.querySelector('div > div').innerHTML = `
                <div style="font-size: 48px; margin-bottom: 16px; animation: spin 1s linear infinite;">⏳</div>
                <style>@keyframes spin { to { transform: rotate(360deg); } }</style>
                <h2 style="margin-bottom: 16px; color: #fff;">Processing...</h2>
                <p style="color: #888; margin-bottom: 24px;">
                    Génération du template et upload en cours...<br>
                    Cela peut prendre quelques secondes.
                </p>
                <div id="progress-bar" style="background: #333; border-radius: 4px; height: 8px; overflow: hidden; margin-bottom: 16px;">
                    <div id="progress-fill" style="background: #ef4444; height: 100%; width: 0%; transition: width 0.3s;"></div>
                </div>
                <p id="progress-text" style="color: #666; font-size: 12px;">Préparation...</p>
            `;
            
            try {
                updateProgress(5, 'Génération du template PNG...');
                
                // Générer le template PNG avec trou transparent
                // Passer params pour avoir accès au texte
                const templateBlob = await generateTemplatePNG(params);
                
                updateProgress(15, 'Préparation de l\'upload...');
                
                // Create form data with video file, template PNG, and params
                const formData = new FormData();
                formData.append('video', state.videoFile);
                formData.append('template', templateBlob, 'template.png');
                const exportParams = {
                    templateWidth: params.templateWidth,
                    templateHeight: params.templateHeight,
                    frameX: params.frameX,
                    frameY: params.frameY,
                    frameWidth: params.frameWidth,
                    frameHeight: params.frameHeight,
                    // Original frame dimensions for video positioning/scaling
                    originalFrameY: params.originalFrameY,
                    originalFrameHeight: params.originalFrameHeight,
                    trimStart: params.trimStart,
                    trimEnd: params.trimEnd,
                    imageScale: params.imageScale,
                    imageOffsetX: params.imageOffsetX,
                    imageOffsetY: params.imageOffsetY
                };
                formData.append('params', JSON.stringify(exportParams));
                
                updateProgress(20, 'Upload de la vidéo et du template...');
                
                const response = await fetch(`${backendUrl}/api/editor/process-video`, {
                    method: 'POST',
                    body: formData
                });
                
                updateProgress(60, 'Traitement FFmpeg...');
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error || 'Erreur serveur');
                }
                
                updateProgress(90, 'Téléchargement...');
                
                // Download the processed video
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = `samourais_meme_${state.currentTemplate}_${Date.now()}.mp4`;
                link.click();
                URL.revokeObjectURL(url);
                
                updateProgress(100, 'Terminé !');
                
                // Success state
                setTimeout(() => {
                    modal.querySelector('div > div').innerHTML = `
                        <div style="font-size: 48px; margin-bottom: 16px;">✅</div>
                        <h2 style="margin-bottom: 16px; color: #fff;">Export réussi !</h2>
                        <p style="color: #888; margin-bottom: 24px;">
                            Ta vidéo a été téléchargée.
                        </p>
                        <button onclick="document.getElementById('export-modal').remove()" 
                                style="padding: 12px 32px; background: #ef4444; border: none; border-radius: 8px; color: #fff; cursor: pointer; font-weight: 600;">
                            Fermer
                        </button>
                    `;
                }, 500);
                
            } catch (error) {
                console.error('Export error:', error);
                modal.querySelector('div > div').innerHTML = `
                    <div style="font-size: 48px; margin-bottom: 16px;">❌</div>
                    <h2 style="margin-bottom: 16px; color: #fff;">Erreur</h2>
                    <p style="color: #ef4444; margin-bottom: 24px;">
                        ${error.message}
                    </p>
                    <div style="display: flex; gap: 12px;">
                        <button onclick="document.getElementById('export-modal').remove()" 
                                style="flex: 1; padding: 12px; background: #333; border: none; border-radius: 8px; color: #fff; cursor: pointer;">
                            Fermer
                        </button>
                        <button onclick="startVideoProcessing()" 
                                style="flex: 1; padding: 12px; background: #ef4444; border: none; border-radius: 8px; color: #fff; cursor: pointer; font-weight: 600;">
                            Réessayer
                        </button>
                    </div>
                `;
            }
        };

        function updateProgress(percent, text) {
            const fill = document.getElementById('progress-fill');
            const textEl = document.getElementById('progress-text');
            if (fill) fill.style.width = percent + '%';
            if (textEl) textEl.textContent = text;
        }

        function showError(message) {
            const modal = document.getElementById('export-modal');
            if (modal) {
                modal.querySelector('div > div').innerHTML = `
                    <div style="font-size: 48px; margin-bottom: 16px;">❌</div>
                    <h2 style="margin-bottom: 16px; color: #fff;">Erreur</h2>
                    <p style="color: #ef4444; margin-bottom: 24px;">
                        ${message}
                    </p>
                    <button onclick="document.getElementById('export-modal').remove()" 
                            style="padding: 12px 32px; background: #333; border: none; border-radius: 8px; color: #fff; cursor: pointer;">
                        Fermer
                    </button>
                `;
            } else {
                note(message, 'error');
            }
        }

        // saveBackendUrl removed — backend is integrated

        // ============================================
        // EVENT LISTENERS
        // ============================================
        function setupEventListeners() {
            // Format selection
            formatBtns.forEach(btn => {
                btn.addEventListener('click', () => {
                    formatBtns.forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    state.currentTemplate = btn.dataset.format;
                    state.imageOffsetX = 0;
                    state.imageOffsetY = 0;
                    state.imageScale = 100;
                    state.frameHeightPercent = 100;
                    
                    imageScaleSlider.value = 100;
                    imageScaleValue.textContent = '100%';
                    frameHeightSlider.value = 100;
                    frameHeightValue.textContent = '100%';
                    
                    // Frame height slider available for all formats (1:1, 4:5, 9:16)
                    frameHeightSection.style.display = 'block';

                    // LOT C — les dimensions annoncées du fichier de sortie
                    // suivent le format choisi.
                    updateExportReadout();

                    updateCanvasSize();
                    createElements();
                    
                    if (state.imageSrc) {
                        addImageToCanvas(state.imageSrc);
                    }
                    if (state.showOverlay && state.overlayText) {
                        addOverlayText();
                    }
                    updateText(state.text);
                });
            });

            // File upload - click
            uploadZone.addEventListener('click', () => fileInput.click());

            // File upload - drag & drop
            uploadZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadZone.style.borderColor = '#ef4444';
            });

            uploadZone.addEventListener('dragleave', () => {
                uploadZone.style.borderColor = '#333';
            });

            uploadZone.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadZone.style.borderColor = '#333';
                const file = e.dataTransfer.files[0];
                if (file && (file.type.startsWith('image/') || file.type.startsWith('video/'))) {
                    loadMedia(file);
                }
            });

            // File input change
            fileInput.addEventListener('change', (e) => {
                const file = e.target.files[0];
                if (file) loadMedia(file);
            });

            // Meme text
            memeTextInput.addEventListener('input', (e) => {
                updateText(e.target.value);
            });

            // Text size slider
            textSizeSlider.addEventListener('input', (e) => {
                updateTextSize(e.target.value);
            });

            // Line height slider
            lineHeightSlider.addEventListener('input', (e) => {
                updateLineHeight(e.target.value);
            });

            // Image scale slider
            imageScaleSlider.addEventListener('input', (e) => {
                updateImageScale(parseInt(e.target.value));
            });

            // Frame height slider (story only)
            frameHeightSlider.addEventListener('input', (e) => {
                updateFrameHeight(parseInt(e.target.value));
            });

            // Select image button
            selectImageBtn.addEventListener('click', () => {
                if (imageObj) {
                    canvas.setActiveObject(imageObj);
                    canvas.renderAll();
                }
            });

            // Overlay toggle
            overlayToggle.addEventListener('click', toggleOverlay);

            // Overlay text
            overlayTextInput.addEventListener('input', (e) => {
                updateOverlayText(e.target.value);
            });

            // Reset
            resetBtn.addEventListener('click', resetAll);

            // Export
            exportBtn.addEventListener('click', exportMeme);

            // Schedule - send to calendar
            scheduleBtn.addEventListener('click', schedulePost);

            // Save meme to viewer gallery
            if (saveMemeBtn) saveMemeBtn.addEventListener('click', saveMemeToViewer);

            // Import source tabs
            const libraryZone = document.getElementById('library-zone');
            importTabs.forEach(tab => {
                tab.addEventListener('click', () => {
                    importTabs.forEach(t => t.classList.remove('active'));
                    tab.classList.add('active');

                    const source = tab.dataset.source;
                    uploadZone.style.display = source === 'local' ? 'block' : 'none';
                    driveZone.style.display = source === 'drive' ? 'block' : 'none';
                    if (libraryZone) libraryZone.style.display = source === 'library' ? 'block' : 'none';
                    if (source === 'library') loadLibraryMedia();
                });
            });

            // Google Drive connect button
            connectDriveBtn.addEventListener('click', openGoogleDrivePicker);

            // Watermark opacity slider
            watermarkOpacitySlider.addEventListener('input', (e) => {
                updateWatermarkOpacity(parseInt(e.target.value));
            });

            // ---- Retouche image (LOT C) ----
            if (cropGroup) {
                cropGroup.addEventListener('click', (e) => {
                    const btn = e.target.closest('.seg__btn');
                    if (!btn) return;
                    const key = btn.dataset.ratio;
                    state.cropRatio = (key === 'free') ? null : (CROP_RATIOS[key] || null);
                    cropGroup.querySelectorAll('.seg__btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    reapplyImageTransforms();
                });
            }

            if (rotateLeftBtn) {
                rotateLeftBtn.addEventListener('click', () => {
                    state.rotation = (state.rotation + 270) % 360;
                    reapplyImageTransforms();
                });
            }
            if (rotateRightBtn) {
                rotateRightBtn.addEventListener('click', () => {
                    state.rotation = (state.rotation + 90) % 360;
                    reapplyImageTransforms();
                });
            }
            if (flipHBtn) {
                flipHBtn.addEventListener('click', () => {
                    state.flipX = !state.flipX;
                    flipHBtn.setAttribute('aria-pressed', String(state.flipX));
                    if (imageObj) { imageObj.set({ flipX: state.flipX }); canvas.requestRenderAll(); }
                    updateImageEditReadouts();
                });
            }
            if (flipVBtn) {
                flipVBtn.addEventListener('click', () => {
                    state.flipY = !state.flipY;
                    flipVBtn.setAttribute('aria-pressed', String(state.flipY));
                    if (imageObj) { imageObj.set({ flipY: state.flipY }); canvas.requestRenderAll(); }
                    updateImageEditReadouts();
                });
            }

            if (adjBrightness) {
                adjBrightness.addEventListener('input', (e) => {
                    state.brightness = parseInt(e.target.value, 10);
                    adjBrightnessValue.textContent = state.brightness;
                    applyImageFilters();
                });
            }
            if (adjContrast) {
                adjContrast.addEventListener('input', (e) => {
                    state.contrast = parseInt(e.target.value, 10);
                    adjContrastValue.textContent = state.contrast;
                    applyImageFilters();
                });
            }
            if (adjSaturation) {
                adjSaturation.addEventListener('input', (e) => {
                    state.saturation = parseInt(e.target.value, 10);
                    adjSaturationValue.textContent = state.saturation;
                    applyImageFilters();
                });
            }
            if (imageResetBtn) {
                imageResetBtn.addEventListener('click', () => resetImageEdits(false));
            }

            // ---- Fichier de sortie (LOT C) ----
            if (imgFormatGroup) {
                imgFormatGroup.addEventListener('click', (e) => {
                    const btn = e.target.closest('.seg__btn');
                    if (!btn) return;
                    state.exportFormat = btn.dataset.imgformat;
                    imgFormatGroup.querySelectorAll('.seg__btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    updateExportReadout();
                });
            }
            if (exportQuality) {
                exportQuality.addEventListener('input', (e) => {
                    state.exportQuality = parseInt(e.target.value, 10);
                    exportQualityValue.textContent = state.exportQuality + '%';
                    updateExportReadout();
                });
            }
            if (sizeGroup) {
                sizeGroup.addEventListener('click', (e) => {
                    const btn = e.target.closest('.seg__btn');
                    if (!btn) return;
                    state.exportScale = parseFloat(btn.dataset.scale) || 1;
                    sizeGroup.querySelectorAll('.seg__btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    updateExportReadout();
                });
            }

            // Window resize
            window.addEventListener('resize', () => {
                updateCanvasSize();
                canvas.renderAll();
            });
        }

        // ============================================
        // LIBRARY — Load scraped media from viewer API
        // ============================================
        let libraryPage = 1;
        let libraryLoading = false;

        async function loadLibraryMedia(append = false) {
            if (libraryLoading) return;
            libraryLoading = true;
            const zone = document.getElementById('library-zone');
            const grid = document.getElementById('library-grid');
            if (!zone || !grid) { libraryLoading = false; return; }

            if (!append) { libraryPage = 1; grid.innerHTML = ''; }

            try {
                const res = await fetch(`/api/viewer/media?page=${libraryPage}&per_page=30&sort=date_desc`);
                const data = await res.json();

                data.items.forEach(item => {
                    const el = document.createElement('div');
                    el.className = 'drive-file' + (item.media_type === 'video' ? ' drive-file-video' : '');
                    el.innerHTML = `<img src="${item.file_url || item.media_url || ''}" alt="${item.caption || ''}" loading="lazy">`;
                    el.onclick = () => loadLibraryItem(item);
                    grid.appendChild(el);
                });

                if (data.items.length === 0 && !append) {
                    grid.innerHTML = '<div style="padding: 24px; text-align: center; color: #666; grid-column: 1/-1;">Aucun media dans la bibliothèque</div>';
                }
            } catch (e) {
                console.error('Failed to load library', e);
                if (!append) grid.innerHTML = '<div style="padding: 24px; text-align: center; color: #666; grid-column: 1/-1;">Erreur de chargement</div>';
            }
            libraryLoading = false;
        }

        async function loadLibraryItem(item) {
            try {
                const url = `/api/editor/media/${item.id}`;
                const response = await fetch(url);

                // Sans ce contrôle, un média dont le fichier a disparu du
                // disque répond 404 avec un corps JSON, et ce JSON était
                // emballé dans un « library_N.jpg » puis passé au décodeur
                // d'images : plan de travail vide, aucune explication.
                if (!response.ok) {
                    let detail = '';
                    try { detail = (await response.json()).error || ''; } catch (e) { /* corps non JSON */ }
                    console.error('[editor] média indisponible', response.status, detail);
                    note(response.status === 404
                        ? 'Ce média n’est plus disponible sur le disque : son fichier a été déplacé ou supprimé. Relance un téléchargement depuis les Médias.'
                        : 'Ce média n’a pas pu être récupéré (erreur ' + response.status + '). Réessaie dans un instant.', 'error');
                    return;
                }

                const blob = await response.blob();
                const file = new File([blob], `library_${item.id}.${item.media_type === 'video' ? 'mp4' : 'jpg'}`, { type: blob.type });

                // Create a synthetic file event and use the existing upload handling
                const dataTransfer = new DataTransfer();
                dataTransfer.items.add(file);
                fileInput.files = dataTransfer.files;
                fileInput.dispatchEvent(new Event('change'));

                // Switch back to local tab
                document.querySelectorAll('.import-tab').forEach(t => t.classList.remove('active'));
                document.querySelector('.import-tab[data-source="local"]').classList.add('active');
                uploadZone.style.display = 'block';
                document.getElementById('library-zone').style.display = 'none';
            } catch (e) {
                console.error('Failed to load library item', e);
                note('Le média n\u2019a pas pu être chargé. Vérifie le fichier, puis réessaie.', 'error');
            }
        }

        // Check for media_id in URL params (from viewer "Edit" button)
        function checkMediaParam() {
            const params = new URLSearchParams(window.location.search);
            const mediaId = params.get('media_id');
            if (mediaId) {
                loadLibraryItem({ id: parseInt(mediaId), media_type: 'image' });
            }
        }

        // ============================================
        // INIT
        // ============================================
        function init() {
            loadLogo(); // Preload logo for watermark
            initCanvas();
            setupEventListeners();
            setupTimelineInteraction();
            // LOT C — les blocs de retouche et de sortie doivent refléter
            // l'état AVANT tout chargement de média.
            syncImageEditControls();
            updateMediaToolsVisibility();
            checkMediaParam();

            // Le fond du plan de travail est peint par Fabric, pas par le
            // CSS : il faut le repeindre à la main à chaque bascule de
            // thème. Trois sources, comme sur l'écran Analytics :
            //   1. le choix explicite, posé sur <html data-theme>
            new MutationObserver(repaintBackdrop).observe(document.documentElement, {
                attributes: true, attributeFilter: ['data-theme']
            });
            //   2. l'évènement émis par la bascule partagée
            document.addEventListener('samourais:themechange', repaintBackdrop);
            //   3. la préférence système, quand aucun choix n'est stocké
            const mq = window.matchMedia('(prefers-color-scheme: dark)');
            if (mq.addEventListener) mq.addEventListener('change', repaintBackdrop);
            else if (mq.addListener) mq.addListener(repaintBackdrop);
        }

        init();
