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

        // Opacité du filigrane, en pourcentage. Constante volontaire : elle ne
        // varie jamais d'une composition à l'autre, et le curseur qui l'exposait
        // n'offrait que le risque de la dérégler. Les compositions enregistrées
        // avant ce changement portent leur propre valeur et restent inchangées.
        const WATERMARK_OPACITY = 50;

        // ============================================
        // FILIGRANE — POSÉ, JAMAIS RÉGLÉ
        // --------------------------------------------
        // Le logo était un objet Fabric SÉLECTIONNABLE et
        // REDIMENSIONNABLE, posé sur deux coordonnées écrites en dur
        // dans chaque gabarit (TEMPLATES[*].watermark) et mises à
        // l'échelle 0,15 quelle que soit la taille du cadre. D'où les
        // « positions et tailles hasardeuses » : chaque composition
        // sortait avec un logo à un endroit différent, parce qu'on
        // pouvait l'attraper d'un doigt en visant le canvas.
        //
        // Il est désormais CALCULÉ à partir du cadre effectif, à
        // chaque reconstruction et à chaque changement de hauteur de
        // cadre, et il n'est ni sélectionnable ni évènementiel. Aucun
        // réglage n'est exposé : ni position, ni taille, ni opacité.
        //
        //   largeur      = 70 % de la LARGEUR DU CADRE ;
        //   dépassement  = 3,5 % de cette même largeur, à droite ET en
        //                  bas — le logo mord le bord du cadre au lieu
        //                  de flotter dedans ;
        //   ancrage      = coin bas-droit (originX/Y right/bottom).
        //
        // Les deux ratios sont relatifs au cadre et non en pixels
        // absolus : le même appoint visuel sur un cadre 1:1, 4:5, 9:16
        // et sur le plateau TikTok plein écran. Sur ce dernier le cadre
        // EST le canvas : le dépassement sort donc du fichier exporté,
        // ce qui est exactement l'effet demandé.
        // Taille et ancrage du filigrane, PAR PLATEAU — un 4:5 et un 9:16
        // n'ont ni la même largeur utile ni la même zone de sécurité.
        //   Instagram : coin bas-droit, avec un léger débord.
        //   TikTok    : bord droit, CENTRÉ EN HAUTEUR, et plus discret encore —
        //               le bas de l'écran y est mangé par les libellés de
        //               l'application (pseudo, légende, boutons).
        const WATERMARK_FRAME_RATIO = 0.45; // Instagram — 0.70 était trop gros, 0.32 trop petit
        const WATERMARK_TIKTOK_RATIO  = 0.30; // TikTok — plus discret qu'Instagram, mais lisible
        const WATERMARK_BLEED_RATIO = 0.035;
        //: TikTok : RETRAIT depuis le bord droit (et non débord). Le cadre y
        //: occupe tout le canvas — déborder revient à sortir de l'image.
        const WATERMARK_TIKTOK_INSET = 0.035;

        /** Largeur cible du filigrane, en fraction de la largeur du cadre.
         *  `p.key` est le même discriminant que `templateOf` : 'ig' | 'tt'. */
        function watermarkRatio(p) {
            return p.key === 'tt' ? WATERMARK_TIKTOK_RATIO : WATERMARK_FRAME_RATIO;
        }

        /** Cadre EFFECTIF du plateau (la hauteur suit « Hauteur du cadre »). */
        function effectiveFrame(p) {
            const template = templateOf(p);
            const frame = template.frame;
            const percent = isFullBleed(p) ? 100 : p.frameHeightPercent;
            const height = Math.round(frame.height * (percent / 100));
            const y = (frame.y + frame.height / 2) - height / 2;
            return { x: frame.x, y: y, width: frame.width, height: height };
        }

        // Boîte d'ENCRE du PNG du logo, en fraction de ses dimensions
        // naturelles — mesurée une fois, au chargement (voir measureLogoInk).
        // samourais_logo_transparent_smooth.png fait 2000×489 mais son tracé
        // ne va que de x=186 à x=1785 et de y=88 à y=407 : 10,7 % de vide à
        // droite et 16,8 % en bas. Sans cette mesure, « 70 % de la largeur
        // du cadre » donnerait un logo VISIBLE à 56 %, et le dépassement
        // serait intégralement mangé par la marge transparente — le logo
        // rentrerait dans le cadre au lieu de le mordre.
        // Les deux consignes ne se vérifient à l'œil que sur l'encre.
        let logoInk = null;

        function measureLogoInk(el) {
            try {
                const w = el.naturalWidth || el.width;
                const h = el.naturalHeight || el.height;
                if (!w || !h) return null;
                const c = document.createElement('canvas');
                c.width = w; c.height = h;
                c.getContext('2d').drawImage(el, 0, 0);
                const d = c.getContext('2d').getImageData(0, 0, w, h).data;
                let x0 = w, y0 = h, x1 = -1, y1 = -1;
                for (let y = 0; y < h; y++) {
                    for (let x = 0; x < w; x++) {
                        if (d[(y * w + x) * 4 + 3] > 20) {
                            if (x < x0) x0 = x;
                            if (x > x1) x1 = x;
                            if (y < y0) y0 = y;
                            if (y > y1) y1 = y;
                        }
                    }
                }
                if (x1 < 0) return null;
                return { l: x0 / w, t: y0 / h, r: (x1 + 1) / w, b: (y1 + 1) / h };
            } catch (e) {
                // Canvas teinté ou décodage incomplet : on retombe sur la
                // boîte de l'image, qui reste correcte, juste plus timide.
                console.warn('[editor] boîte d’encre du logo non mesurable', e);
                return null;
            }
        }

        /** Pose (ou repose) le filigrane du plateau sur le coin bas-droit du cadre. */
        function placeWatermark(p) {
            if (!p.watermark) return;
            const f = effectiveFrame(p);
            const bleed = f.width * WATERMARK_BLEED_RATIO;
            const target = f.width * watermarkRatio(p);
            // `width` d'un fabric.Image est la largeur NATURELLE de la
            // source : le facteur d'échelle s'en déduit directement.
            const natural = p.watermark.width || 1;
            const naturalH = p.watermark.height || 1;
            // Repli texte (logo pas encore décodé) : pas de marge à corriger.
            const ink = (p.watermark.type === 'image' && logoInk)
                ? logoInk
                : { l: 0, t: 0, r: 1, b: 1 };
            const scale = target / (natural * (ink.r - ink.l));
            // On vise le coin de l'ENCRE ; l'objet, lui, est ancré sur le
            // coin de l'IMAGE — d'où le rattrapage de la marge transparente.
            const padRight = natural * (1 - ink.r) * scale;
            const padBottom = naturalH * (1 - ink.b) * scale;
            // TikTok : bord droit, CENTRÉ EN HAUTEUR — et RENTRÉ, pas débordé.
            // Le `bleed` d'Instagram fait mordre le logo hors du cadre : c'est
            // voulu là-bas, où le cadre flotte dans une marge. Sur TikTok le
            // cadre EST le canvas : le même débord poussait le logo à cheval
            // sur le bord, donc coupé à l'export. On l'INVERSE en retrait.
            const surTikTok = (p.key === 'tt');
            const retrait = f.width * WATERMARK_TIKTOK_INSET;
            const ancrage = surTikTok
                ? {
                    originX: 'right',
                    originY: 'center',
                    left: CANVAS_PADDING + f.x + f.width - retrait + padRight,
                    top:  CANVAS_PADDING + f.y + f.height / 2
                  }
                : {
                    originX: 'right',
                    originY: 'bottom',
                    left: CANVAS_PADDING + f.x + f.width + bleed + padRight,
                    top:  CANVAS_PADDING + f.y + f.height + bleed + padBottom
                  };
            p.watermark.set({
                originX: ancrage.originX,
                originY: ancrage.originY,
                left: ancrage.left,
                top: ancrage.top,
                scaleX: scale,
                scaleY: scale,
                opacity: state.watermarkOpacity / 100,
                // Le geste qui déréglait le logo n'existe plus.
                selectable: false,
                evented: false,
                hasControls: false,
                hasBorders: false
            });
            p.watermark.setCoords();
        }

        /** Construit l'objet filigrane du plateau et le pose. */
        function buildWatermark(p) {
            if (p.watermark) p.canvas.remove(p.watermark);
            if (logoImage) {
                p.watermark = new fabric.Image(logoImage.getElement(), {});
            } else {
                // Repli tant que le PNG n'est pas décodé : même ancrage,
                // même règle de taille — le texte est remplacé par le
                // logo dès que loadLogo() aboutit.
                p.watermark = new fabric.Text('SAMOURAÏS', {
                    fontSize: 120,
                    fontFamily: 'Impact, sans-serif',
                    fontWeight: '800',
                    fill: '#ffffff',
                    stroke: '#333333',
                    strokeWidth: 2
                });
            }
            placeWatermark(p);
            p.canvas.add(p.watermark);
            return p.watermark;
        }

        // ============================================
        // GRILLE DES TIERS — REPÈRE DE CADRAGE
        // --------------------------------------------
        // Quatre filets blancs à 50 % d'opacité sur le cadre, VISIBLES
        // PENDANT LE CADRAGE UNIQUEMENT (étape 2). Ils vivent sur le
        // canvas Fabric et pas en surimpression CSS, parce qu'ils
        // doivent suivre le cadre EFFECTIF — donc la hauteur de cadre,
        // le format et le plateau — au pixel du gabarit, pas au pixel
        // de l'écran.
        //
        // Ce sont des REPÈRES, jamais du contenu : `excludeFromExport`
        // les retire de toDataURL/toJSON quoi qu'il arrive, et
        // renderCanvasToDataURL les masque en plus par ceinture.
        // ============================================
        function buildThirdsGrid(p) {
            p.thirdsLines.forEach(function(l) { p.canvas.remove(l); });
            p.thirdsLines = [];
            // `createElements()` reconstruit tout le plateau — au changement
            // de format, au « Réinitialiser » global. La grille doit
            // renaître dans l'état de l'ÉTAPE COURANTE, sinon elle
            // disparaissait dès qu'on changeait de format en plein cadrage.
            // (`wizStep` est initialisé à l'évaluation du script, bien avant
            // le premier appel : `init()` est la dernière ligne du fichier.)
            const visible = (wizStep === 2);
            for (var i = 0; i < 4; i++) {
                p.thirdsLines.push(new fabric.Line([0, 0, 0, 0], {
                    stroke: 'rgba(255,255,255,0.5)',
                    strokeWidth: 2,
                    selectable: false,
                    evented: false,
                    visible: visible,
                    excludeFromExport: true
                }));
            }
            p.thirdsLines.forEach(function(l) { p.canvas.add(l); });
            placeThirdsGrid(p);
        }

        function placeThirdsGrid(p) {
            if (!p.thirdsLines || p.thirdsLines.length !== 4) return;
            const f = effectiveFrame(p);
            const x = CANVAS_PADDING + f.x;
            const y = CANVAS_PADDING + f.y;
            const coords = [
                [x + f.width / 3, y, x + f.width / 3, y + f.height],
                [x + (f.width * 2) / 3, y, x + (f.width * 2) / 3, y + f.height],
                [x, y + f.height / 3, x + f.width, y + f.height / 3],
                [x, y + (f.height * 2) / 3, x + f.width, y + (f.height * 2) / 3]
            ];
            // Un filet d'UN pixel À L'ÉCRAN, quel que soit le zoom de la
            // vue : le gabarit fait 1080 unités de large pour ~250px
            // affichés, un `strokeWidth: 1` en unités de gabarit serait
            // quatre fois trop fin pour se voir.
            const stroke = 1 / (p.scale || 1);
            p.thirdsLines.forEach(function(line, i) {
                const c = coords[i];
                line.set({ x1: c[0], y1: c[1], x2: c[2], y2: c[3], strokeWidth: stroke });
                line.setCoords();
            });
        }

        /** Montre ou cache la grille sur les deux plateaux. */
        function showThirdsGrid(visible) {
            eachPane(function(p) {
                if (!p.canvas || !p.thirdsLines) return;
                placeThirdsGrid(p);
                p.thirdsLines.forEach(function(l) { l.set({ visible: !!visible }); });
                p.canvas.requestRenderAll();
            });
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
        // PLATEAU TIKTOK — PLEIN ÉCRAN
        // --------------------------------------------
        // Demande du propriétaire : « pour tiktok il faut que ce soit plein
        // écran y a pas de template blanc autour ». Un TikTok réel n'a NI
        // fond blanc, NI cadre, NI bandeau de texte : le média couvre les
        // 1080×1920 (ajustement « cover », l'excédent est rogné) et le texte
        // POV se pose PAR-DESSUS. Ce gabarit dédié remplace 'story' pour le
        // plateau TikTok UNIQUEMENT — le plateau Instagram garde TEMPLATES.
        //   - frame = tout le canvas (0,0,1080,1920, sans arrondi) ;
        //   - fullBleed:true fait sauter le fond blanc, le cadre pointillé
        //     et le bandeau dans createElements() ;
        //   - textArea à 0 : plus de zone de texte de gabarit (les points
        //     d'accroche du magnétisme retombent sur les bords du canvas) ;
        //   - filigrane : position basse inchangée (mêmes coordonnées que
        //     l'ancien gabarit 'story').
        const TIKTOK_TEMPLATE = {
            width: 1080,
            height: 1920,
            fullBleed: true,
            frame: { x: 0, y: 0, width: 1080, height: 1920, radius: 0 },
            textArea: { x: 0, y: 0, width: 1080, maxY: 0 },
            watermark: { x: 956, y: 1700 }
        };

        // ============================================
        // STATE
        // ============================================
        const state = {
            // Format du canvas INSTAGRAM uniquement (le canvas TikTok est
            // fixe en 'story' 1080×1920). Défaut : Portrait 4:5.
            currentTemplate: 'portrait',
            // Media state (image or video)
            mediaType: null, // 'image' or 'video'
            imageSrc: null,
            imageName: '',
            imageSize: 0,
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
            // Texte TikTok « POV » — rendu sur le canvas TikTok uniquement.
            povText: '',
            // 'outline' = texte blanc a contour noir (le style TikTok le plus courant),
            // 'light' = fond blanc/texte noir, 'dark' = fond noir translucide/texte blanc
            povStyle: 'outline',
            // Watermark state — opacité constante, voir WATERMARK_OPACITY
            watermarkOpacity: WATERMARK_OPACITY,
            // ---- Média choisi dans la bibliothèque scrappée ----
            // Sert la ligne d'info de l'étape 1, l'anneau de sélection de
            // la grille, et le PRÉ-REMPLISSAGE du bandeau à l'étape 3 :
            // `phrase` est la colonne écrite par le Tri rapide de la
            // galerie (POST /api/viewer/media/<id>/phrase).
            libraryItem: null,
            phrase: '',
            phraseUsed: false,
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
        // PLATEAUX (ÉDITEUR DOUBLE)
        // --------------------------------------------
        // UNE composition, DEUX rendus : le média, le texte et la retouche
        // alimentent les deux canvas ; le CADRAGE (position/zoom de l'image
        // dans le cadre) est PAR plateau — un recadrage 4:5 et un recadrage
        // 9:16 ne peuvent pas être identiques. Chaque plateau porte donc ses
        // objets Fabric ET son état de cadrage.
        // ============================================
        function makePane(key, label, platform, canvasId, stageId) {
            return {
                key, label, platform, canvasId, stageId,
                enabled: true,
                // Objets Fabric propres au plateau
                canvas: null,
                textBox: null, imageObj: null, overlayTextObj: null,
                frameRect: null, frameBorder: null, watermark: null,
                templateBg: null, clipRect: null, povObj: null,
                snapLines: [], thirdsLines: [],
                // Cadrage par plateau
                imageScale: 100,
                imageOffsetX: 0,
                imageOffsetY: 0,
                frameHeightPercent: 100,
                scale: 1
            };
        }

        const panes = {
            ig: makePane('ig', 'Instagram', 'instagram', 'meme-canvas-ig', 'stage-ig'),
            tt: makePane('tt', 'TikTok', 'tiktok', 'meme-canvas-tt', 'stage-tt')
        };

        function eachPane(fn) { fn(panes.ig); fn(panes.tt); }
        function activePanes() { return [panes.ig, panes.tt].filter(p => p.enabled); }

        /** Clé de template du plateau : IG suit les boutons de format, TikTok est fixe.
         *  La clé reste 'story' pour TikTok : c'est l'étiquette DONNÉE (template_format
         *  du Viewer et du Calendrier, ratio 9:16) — le RENDU, lui, passe par
         *  templateOf() qui sert le gabarit plein écran dédié. */
        function templateKeyOf(p) { return p.key === 'ig' ? state.currentTemplate : 'story'; }
        function templateOf(p) { return p.key === 'ig' ? TEMPLATES[state.currentTemplate] : TIKTOK_TEMPLATE; }
        /** Vrai pour le plateau plein écran (TikTok) : pas de gabarit autour du média. */
        function isFullBleed(p) { return !!templateOf(p).fullBleed; }

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

        // ---- Éditeur double ----
        const stagesEl = document.getElementById('stages');
        const stageDimsIG = document.getElementById('stage-ig-dims');
        const povTextInput = document.getElementById('pov-text');
        const povStyleGroup = document.getElementById('pov-style-group');
        const scheduleDialog = document.getElementById('schedule-dialog');
        const scheduleForm = document.getElementById('schedule-form');
        const scheduleDatetime = document.getElementById('schedule-datetime');
        const scheduleCheckIG = document.getElementById('schedule-check-ig');
        const scheduleCheckTT = document.getElementById('schedule-check-tt');
        const scheduleIGDims = document.getElementById('schedule-ig-dims');

        // ============================================
        // FABRIC CANVAS — un par plateau (voir `panes`)
        // ============================================

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
            eachPane(function(p) {
                if (!p.canvas) return;
                p.canvas.backgroundColor = artboardBackdrop();
                p.canvas.requestRenderAll();
            });
        }

        function initCanvases() {
            // Custom controls style - larger and more visible (prototype
            // partagé : une seule fois pour les deux canvas)
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

            eachPane(function(p) {
                p.canvas = new fabric.Canvas(p.canvasId, {
                    backgroundColor: artboardBackdrop(),
                    selection: true,
                    preserveObjectStacking: true,
                    perPixelTargetFind: false // Click on bounding box, not just visible pixels
                });
                updateCanvasSize(p);
                createElements(p);
                setupCanvasHoverEffects(p);
                setupSnapping(p);
            });
        }

        function setupCanvasHoverEffects(p) {
            // Show border on hover
            p.canvas.on('mouse:over', function(e) {
                if (e.target && e.target.selectable) {
                    e.target._showBorder = true;
                    e.target.set('dirty', true);
                    p.canvas.renderAll();
                }
            });

            p.canvas.on('mouse:out', function(e) {
                if (e.target && e.target._showBorder) {
                    e.target._showBorder = false;
                    e.target.set('dirty', true);
                    p.canvas.renderAll();
                }
            });
        }

        // ============================================
        // SNAPPING / MAGNET SYSTEM
        // ============================================
        const SNAP_THRESHOLD = 15; // Distance in pixels to trigger snap

        function setupSnapping(p) {
            p.canvas.on('object:moving', function(e) {
                const obj = e.target;
                if (!obj) return;

                const template = templateOf(p);
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
                clearSnapLines(p);

                // Check X snapping (left edge)
                for (const snapX of snapPointsX) {
                    if (Math.abs(objLeft - snapX) < SNAP_THRESHOLD) {
                        obj.set('left', snapX);
                        snappedX = true;
                        showSnapLine(p, 'vertical', snapX);
                        break;
                    }
                }

                // Check X snapping (center) - only for text objects
                if (!snappedX && (obj === p.textBox || obj === p.overlayTextObj || obj === p.povObj)) {
                    const templateCenterX = offset + template.width / 2;
                    if (Math.abs(objCenterX - templateCenterX) < SNAP_THRESHOLD) {
                        obj.set('left', templateCenterX - (obj.width * (obj.scaleX || 1)) / 2);
                        snappedX = true;
                        showSnapLine(p, 'vertical', templateCenterX);
                    }
                }

                // Check Y snapping (top edge)
                for (const snapY of snapPointsY) {
                    if (Math.abs(objTop - snapY) < SNAP_THRESHOLD) {
                        obj.set('top', snapY);
                        snappedY = true;
                        showSnapLine(p, 'horizontal', snapY);
                        break;
                    }
                }

                p.canvas.renderAll();
            });

            p.canvas.on('object:modified', function() {
                clearSnapLines(p);
                p.canvas.renderAll();
            });

            p.canvas.on('mouse:up', function() {
                clearSnapLines(p);
                p.canvas.renderAll();
            });
        }

        function showSnapLine(p, orientation, position) {
            const template = templateOf(p);
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

            p.canvas.add(line);
            p.snapLines.push(line);
        }

        function clearSnapLines(p) {
            p.snapLines.forEach(line => p.canvas.remove(line));
            p.snapLines = [];
        }

        // ---- LOT B — aperçu collant sous 900px ----
        // Sous ce seuil l'aperçu est un bandeau de ~45dvh qui ne montre
        // qu'UN plateau ; la marge de travail desktop (CANVAS_PADDING =
        // 350px de chaque côté) y réduirait le gabarit à ~120px de large.
        // On cadre donc la VUE sur le gabarit + une marge réduite, par
        // translation du viewport Fabric — les coordonnées des objets ne
        // bougent pas, seule la fenêtre d'affichage change.
        const MOBILE_VIEW_MARGIN = 28;
        // Même principe au-dessus de 900px : la marge de manipulation reste
        // ENTIÈRE dans les coordonnées (les poignées de Fabric continuent de
        // vivre dedans), seule la fenêtre affichée est resserrée.
        const DESK_VIEW_MARGIN = 90;
        const mobileViewMq = window.matchMedia('(max-width: 899.98px)');

        function updateCanvasSize(p) {
            const template = templateOf(p);
            const { width, height } = template;
            const container = document.querySelector('.preview-area');

            if (mobileViewMq.matches) {
                // Un seul plateau visible, cadré sur le gabarit.
                const visW = width + (MOBILE_VIEW_MARGIN * 2);
                const visH = height + (MOBILE_VIEW_MARGIN * 2);
                const stage = document.getElementById(p.stageId);
                const head = stage ? stage.querySelector('.stage-head') : null;
                // On mesure la BOÎTE DU PLATEAU, pas l'aire entière : elle
                // exclut d'elle-même le basculeur, le mode d'emploi et les
                // marges. Additionner à la main les hauteurs du chrome,
                // c'était refaire le calcul de la CSS en JS — et se
                // tromper dès qu'un des blocs bougeait.
                const boxW = (stage && stage.clientWidth) || container.clientWidth;
                const boxH = (stage && stage.clientHeight) || container.clientHeight;
                const headH = (head && head.offsetHeight) ? head.offsetHeight : 20;
                const maxW = Math.max(120, boxW - 16);
                const maxH = Math.max(120, boxH - headH - 20);
                p.scale = Math.min(maxW / visW, maxH / visH, 0.4);
                p.canvas.setWidth(visW * p.scale);
                p.canvas.setHeight(visH * p.scale);
                const pan = (CANVAS_PADDING - MOBILE_VIEW_MARGIN) * p.scale;
                p.canvas.setViewportTransform([p.scale, 0, 0, p.scale, -pan, -pan]);
                return;
            }

            // Deux plateaux côte à côte : chacun reçoit sa part de la
            // largeur. En pile (colonne, sous 1200px), chacun a tout.
            const stacked = stagesEl
                && getComputedStyle(stagesEl).flexDirection === 'column';
            const visible = stacked ? 1 : Math.max(1, activePanes().length);
            const baseW = (stagesEl ? stagesEl.clientWidth : container.clientWidth);
            const baseH = (stagesEl && stagesEl.clientHeight)
                ? stagesEl.clientHeight : container.clientHeight;
            const stage = document.getElementById(p.stageId);
            const head = stage ? stage.querySelector('.stage-head') : null;
            const headH = (head && head.offsetHeight) ? head.offsetHeight : 20;
            // En pile (900 → 1200px), les deux plateaux se partagent la
            // HAUTEUR comme ils se partagent la largeur côte à côte. Sans
            // ça, chacun prenait toute la hauteur : le premier gabarit
            // sortait du cadre par le bas et il fallait faire défiler pour
            // découvrir qu'il y en avait un second.
            const rows = stacked ? Math.max(1, activePanes().length) : 1;
            const maxW = Math.max(160, baseW / visible - 40);
            const maxH = Math.max(160, baseH / rows - headH - 28);

            // Le plan de travail Fabric porte une marge de manipulation de
            // CANVAS_PADDING (350px) DE CHAQUE CÔTÉ. Le parcours ayant pris
            // 510px de chrome (rail + panneau), l'afficher entière ramenait
            // le gabarit à ~180px de large sur un écran de 1280 : on voyait
            // surtout du vide. On cadre donc la VUE sur le gabarit plus une
            // marge réduite, exactement comme la branche mobile ci-dessus —
            // par TRANSLATION du viewport : les coordonnées des objets ne
            // bougent pas d'un pixel, seule la fenêtre d'affichage change,
            // et renderCanvasToDataURL repose de toute façon la transformée
            // identité avant de découper.
            const visW = width + (DESK_VIEW_MARGIN * 2);
            const visH = height + (DESK_VIEW_MARGIN * 2);

            p.scale = Math.min(maxW / visW, maxH / visH, 0.4);

            p.canvas.setWidth(visW * p.scale);
            p.canvas.setHeight(visH * p.scale);
            const pan = (CANVAS_PADDING - DESK_VIEW_MARGIN) * p.scale;
            p.canvas.setViewportTransform([p.scale, 0, 0, p.scale, -pan, -pan]);
        }

        function updateAllCanvasSizes() {
            eachPane(function(p) {
                if (!p.canvas) return;
                updateCanvasSize(p);
                // L'épaisseur des filets de tiers est exprimée en pixels
                // d'ÉCRAN : elle se recalcule à chaque changement d'échelle.
                placeThirdsGrid(p);
                p.canvas.renderAll();
            });
        }

        function createElements(p) {
            p.canvas.clear();
            p.canvas.backgroundColor = artboardBackdrop();

            const template = templateOf(p);
            const frame = template.frame;
            const offset = CANVAS_PADDING; // Offset for all elements
            // Plein écran (TikTok) : pas de gabarit blanc, pas de cadre, pas
            // de bandeau — le média couvre tout, le POV se pose par-dessus.
            const fullBleed = !!template.fullBleed;

            // Calculate effective frame height (for story template customization)
            // — un plateau plein écran ne se rétrécit pas : il resterait des
            // bandes, ce que ce plateau interdit par définition.
            const effectiveFrameHeight = Math.round(
                frame.height * ((fullBleed ? 100 : p.frameHeightPercent) / 100));

            // Calculate Y position to keep frame centered
            const originalCenterY = frame.y + frame.height / 2;
            const effectiveFrameY = originalCenterY - effectiveFrameHeight / 2;

            // Template background — white for the meme template, BLACK for the
            // full-bleed pane : c'est l'écran TikTok éteint, jamais du blanc,
            // et c'est ce noir qui sort à l'export si le média est dézoomé.
            p.templateBg = new fabric.Rect({
                left: offset,
                top: offset,
                width: template.width,
                height: template.height,
                fill: fullBleed ? '#000000' : '#ffffff',
                selectable: false,
                evented: false
            });
            p.canvas.add(p.templateBg);

            // Create the clip path (absolute position - stays fixed)
            p.clipRect = new fabric.Rect({
                left: frame.x + offset,
                top: effectiveFrameY + offset,
                width: frame.width,
                height: effectiveFrameHeight,
                rx: frame.radius,
                ry: frame.radius,
                absolutePositioned: true
            });

            // Frame placeholder (gray background when no image)
            p.frameRect = new fabric.Rect({
                left: frame.x + offset,
                top: effectiveFrameY + offset,
                width: frame.width,
                height: effectiveFrameHeight,
                rx: frame.radius,
                ry: frame.radius,
                // Plein écran : le « pas encore de média » est noir, pas gris.
                fill: fullBleed ? '#000000' : '#f0f0f0',
                selectable: false,
                evented: false
            });
            p.canvas.add(p.frameRect);

            // Frame border - interactive indicator (shows where image area is)
            p.frameBorder = new fabric.Rect({
                left: frame.x + offset,
                top: effectiveFrameY + offset,
                width: frame.width,
                height: effectiveFrameHeight,
                rx: frame.radius,
                ry: frame.radius,
                fill: 'transparent',
                // Plein écran : AUCUN cadre — le repère pointillé disparaît.
                stroke: fullBleed ? 'transparent' : '#ddd',
                strokeWidth: 2,
                strokeDashArray: [8, 4],
                selectable: false,
                evented: false
            });
            p.canvas.add(p.frameBorder);

            // Text box — bandeau du gabarit meme. Le plateau plein écran n'en
            // a PAS : ni bandeau, ni placeholder « Tape ton texte... » — le
            // texte posé sur TikTok, c'est le POV. Tous les consommateurs de
            // p.textBox testent déjà sa présence.
            if (fullBleed) {
                p.textBox = null;
            } else {
            const textArea = template.textArea;
            p.textBox = new fabric.Textbox(state.text || 'Tape ton texte...', {
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
            p.canvas.add(p.textBox);

            // Sync textBox changes back to state, input AND the twin pane :
            // le texte est UNE donnée de la composition, rendue deux fois.
            p.textBox.on('changed', function() {
                const newText = p.textBox.text === 'Tape ton texte...' ? '' : p.textBox.text;
                state.text = newText;
                memeTextInput.value = newText;
                eachPane(function(other) {
                    if (other === p || !other.textBox) return;
                    other.textBox.set({
                        text: newText || 'Tape ton texte...',
                        fill: '#000000'
                    });
                    other.canvas.renderAll();
                });
            });
            }

            // Repères de cadrage, sous le filigrane et sous le texte.
            buildThirdsGrid(p);

            // Filigrane — calculé sur le cadre, jamais réglé (voir
            // placeWatermark). L'ancien bloc posait deux coordonnées
            // écrites en dur et rendait l'objet déplaçable.
            buildWatermark(p);

            // Le bloc POV appartient au canvas TikTok : il est recréé après
            // chaque reconstruction des éléments de ce plateau.
            if (p.key === 'tt') {
                p.povObj = null;
                if (state.povText) ensurePovObject();
            }

            p.canvas.renderAll();
        }

        // ============================================
        // MEDIA HANDLING (IMAGE + VIDEO)
        // ============================================
        function loadMedia(file) {
            const isVideo = file.type.startsWith('video/');
            state.mediaType = isVideo ? 'video' : 'image';
            // `loadLibraryItem` renseigne la fiche JUSTE APRÈS avoir
            // déclenché ce chargement : on ne l'efface donc que pour un
            // fichier qui ne vient PAS de la bibliothèque.
            if (!file.name || file.name.indexOf('library_') !== 0) {
                state.libraryItem = null;
                state.phrase = '';
                state.phraseUsed = false;
            }
            
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
                // Le cadrage repart de zéro SUR CHAQUE plateau.
                eachPane(function(p) {
                    p.imageScale = 100;
                    p.imageOffsetX = 0;
                    p.imageOffsetY = 0;
                });
                // LOT C — une nouvelle image repart d'une retouche vierge :
                // garder le recadrage de la précédente n'aurait aucun sens.
                Object.assign(state, IMAGE_EDIT_DEFAULTS);
                syncImageEditControls();
                updateMediaToolsVisibility();

                // Hide video timeline
                timelineContainer.style.display = 'none';

                updateUploadZone();
                addImageToAllPanes(e.target.result);
                
                imageScaleSection.style.display = 'block';
                imageScaleSlider.value = 100;
                imageScaleValue.textContent = '100%';
                selectImageBtn.style.display = 'block';
                
                exportBtn.disabled = false;
                exportBtn.textContent = 'Télécharger le meme';
                scheduleBtn.disabled = false;
                if (saveMemeBtn) saveMemeBtn.disabled = false;
            };
            reader.readAsDataURL(file);
        }

        function loadVideo(file) {
            state.videoFile = file;
            state.imageName = file.name;
            state.imageSize = file.size;
            eachPane(function(p) {
                p.imageScale = 100;
                p.imageOffsetX = 0;
                p.imageOffsetY = 0;
            });
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
                exportBtn.textContent = 'Exporter la vidéo';
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

                    addImageToAllPanes(dataURL);
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
                
                // Update canvas preview — la frame courante alimente les
                // DEUX plateaux, comme le média importé.
                const tempCanvas = document.createElement('canvas');
                tempCanvas.width = videoSource.videoWidth;
                tempCanvas.height = videoSource.videoHeight;
                const ctx = tempCanvas.getContext('2d');
                ctx.drawImage(videoSource, 0, 0);
                const frameURL = tempCanvas.toDataURL('image/jpeg', 0.8);

                eachPane(function(p) {
                    if (!p.imageObj) return;
                    p.imageObj.setSrc(frameURL, () => {
                        p.canvas.renderAll();
                    });
                });
                
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
            eachPane(function(p) {
                if (p.imageObj) {
                    p.canvas.remove(p.imageObj);
                    p.imageObj = null;
                }
                p.canvas.requestRenderAll();
            });
            state.imageSrc = null;
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

        /** Le même média alimente LES DEUX plateaux, chacun avec SON cadrage. */
        let rejectionNotified = false;
        function addImageToAllPanes(src) {
            rejectionNotified = false;
            eachPane(function(p) { addImageToCanvas(p, src); });
        }

        function addImageToCanvas(p, src) {
            // Fabric 5 passe `isError` en second argument et rend malgré tout
            // un objet Image — de dimensions 0×0. Sans ce contrôle, un fichier
            // qui n'est pas une image (extension trompeuse, média tronqué)
            // produisait un plan de travail VIDE, sans un mot à l'écran, puis
            // un export « réussi » ne contenant que le gabarit. L'échec doit
            // se voir : c'est le défaut que l'audit reproche déjà à FFmpeg.
            fabric.Image.fromURL(src, (img, isError) => {
                if (isError || !img || !img.width || !img.height) {
                    // Deux plateaux, un seul message : l'échec de décodage
                    // est celui du FICHIER, pas d'un canvas.
                    if (!rejectionNotified) {
                        rejectionNotified = true;
                        rejectUnusableImage();
                    }
                    return;
                }

                if (p.imageObj) {
                    p.canvas.remove(p.imageObj);
                }

                const template = templateOf(p);
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
                const finalScale = baseScale * (p.imageScale / 100);

                // Center the image in the frame (with offset)
                const centerX = frame.x + frame.width / 2 + offset;
                const centerY = frame.y + frame.height / 2 + offset;

                img.set({
                    left: centerX + p.imageOffsetX,
                    top: centerY + p.imageOffsetY,
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
                    clipPath: p.clipRect,
                    // Visual styling
                    strokeWidth: 0,
                    borderColor: '#ef4444',
                    borderDashArray: [5, 5],
                    hoverCursor: 'move',
                    moveCursor: 'move'
                });

                // Store base scale for slider calculations
                img._baseScale = baseScale;

                p.imageObj = img;
                p.canvas.add(p.imageObj);

                // Reorder layers - templateBg at very back, then frameRect, then image
                p.canvas.sendToBack(p.imageObj);
                p.canvas.sendToBack(p.frameBorder);
                p.canvas.sendToBack(p.frameRect);
                p.canvas.sendToBack(p.templateBg);
                if (p.textBox) p.canvas.bringToFront(p.textBox); // pas de bandeau en plein écran
                if (p.povObj) p.canvas.bringToFront(p.povObj);
                p.canvas.bringToFront(p.watermark);

                if (p.overlayTextObj) {
                    p.canvas.bringToFront(p.overlayTextObj);
                }

                // LOT C — réglages (luminosité / contraste / saturation)
                // réappliqués : ils survivent au changement de format.
                applyImageFilters();
                updateImageEditReadouts();

                // Track image movement — le cadrage est PAR plateau.
                p.imageObj.on('moving', function() {
                    const template = templateOf(p);
                    const frame = template.frame;
                    const offset = CANVAS_PADDING;
                    const centerX = frame.x + frame.width / 2 + offset;
                    const centerY = frame.y + frame.height / 2 + offset;

                    p.imageOffsetX = this.left - centerX;
                    p.imageOffsetY = this.top - centerY;
                });

                // Track image scaling
                p.imageObj.on('scaling', function() {
                    const currentScale = this.scaleX;
                    const baseScale = this._baseScale;
                    const percentage = Math.round((currentScale / baseScale) * 100);

                    p.imageScale = percentage;
                    imageScaleSlider.value = Math.min(200, Math.max(50, percentage));
                    imageScaleValue.textContent = percentage + '%';
                });

                p.canvas.renderAll();
            }, { crossOrigin: 'anonymous' });
        }

        // Le slider agit sur LES DEUX plateaux (même geste qu'avant) ; le
        // zoom par poignées, lui, reste propre au plateau manipulé.
        /** Applique un zoom à UN plateau. `p.imageScale` existait déjà par
         *  plateau : seul le contrôle était partagé, ce qui forçait les deux
         *  formats à porter le même cadrage. */
        function appliquerZoom(p, percentage) {
            p.imageScale = percentage;
            if (p.imageObj && p.imageObj._baseScale) {
                const newScale = p.imageObj._baseScale * (percentage / 100);
                p.imageObj.set({ scaleX: newScale, scaleY: newScale });
                p.canvas.renderAll();
            }
        }

        /** Remet les deux curseurs et leurs valeurs au diapason de l'état. */
        function syncZoomControls() {
            [['ig', panes.ig], ['tt', panes.tt]].forEach(function(paire) {
                const suffixe = paire[0], pane = paire[1];
                const curseur = document.getElementById('zoom-' + suffixe);
                const lecture = document.getElementById('zoom-readout-' + suffixe);
                const valeur = Math.round(pane.imageScale || 100);
                if (curseur) curseur.value = valeur;
                if (lecture) lecture.textContent = valeur + ' %';
            });
        }

        // Conservé pour les appels existants (réinitialisation, chargement
        // d'un média) : met les DEUX plateaux à la même valeur de départ.
        function updateImageScale(percentage) {
            if (imageScaleValue) imageScaleValue.textContent = percentage + '%';
            eachPane(function(p) { appliquerZoom(p, percentage); });
            syncZoomControls();
        }

        function updateFrameHeight(percentage) {
            frameHeightValue.textContent = percentage + '%';
            eachPane(function(p) {
                // Plein écran (TikTok) : rétrécir le cadre recréerait des
                // bandes autour du média — ce plateau reste à 100 %.
                if (isFullBleed(p)) return;
                p.frameHeightPercent = percentage;

                const template = templateOf(p);
                const frame = template.frame;
                const offset = CANVAS_PADDING;

                // Calculer la nouvelle hauteur effective
                const effectiveFrameHeight = Math.round(frame.height * (percentage / 100));

                // Calculer le centre original du cadre
                const originalCenterY = frame.y + frame.height / 2;

                // Calculer la nouvelle position Y pour garder le cadre centré
                const newFrameY = originalCenterY - effectiveFrameHeight / 2;

                // Mettre à jour les dimensions ET la position du cadre
                if (p.clipRect) {
                    p.clipRect.set({
                        top: newFrameY + offset,
                        height: effectiveFrameHeight
                    });
                }
                if (p.frameRect) {
                    p.frameRect.set({
                        top: newFrameY + offset,
                        height: effectiveFrameHeight
                    });
                }
                if (p.frameBorder) {
                    p.frameBorder.set({
                        top: newFrameY + offset,
                        height: effectiveFrameHeight
                    });
                }

                // Le filigrane est ancré au cadre : il descend et remonte
                // avec lui. Sans cet appel il restait collé à l'ancienne
                // hauteur, seul cas où sa position redevenait « hasardeuse ».
                placeWatermark(p);
                placeThirdsGrid(p);

                p.canvas.renderAll();
            });
        }

        function updateUploadZone() {
            // Point de passage OBLIGÉ de tout changement de média
            // (chargement image, chargement vidéo, retrait) : c'est ici
            // que le parcours réévalue son verrou — « Continuer » ne
            // s'allume que quand il y a quelque chose à cadrer.
            if (typeof wizSync === 'function') wizSync();
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
            eachPane(function(p) {
                p.imageScale = 100;
                p.imageOffsetX = 0;
                p.imageOffsetY = 0;
            });

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

            eachPane(function(p) {
                if (p.imageObj) {
                    p.canvas.remove(p.imageObj);
                    p.imageObj = null;
                }
            });

            // Hide all media-related UI
            imageScaleSection.style.display = 'none';
            selectImageBtn.style.display = 'none';
            timelineContainer.style.display = 'none';
            mediaTypeBadge.style.display = 'none';
            
            state.libraryItem = null;
            state.phrase = '';
            state.phraseUsed = false;
            if (typeof markLibrarySelection === 'function') markLibrarySelection();

            updateUploadZone();
            // Plus de média : les trois dernières étapes n'ont plus d'objet.
            if (typeof goStep === 'function') goStep(1);
            exportBtn.disabled = true;
            exportBtn.textContent = 'Télécharger le meme';
            scheduleBtn.disabled = true;
            if (saveMemeBtn) saveMemeBtn.disabled = true;
            eachPane(function(p) { p.canvas.renderAll(); });
        };

        // ============================================
        // TEXT HANDLING
        // ============================================
        function updateText(text) {
            state.text = text;
            eachPane(function(p) {
                if (!p.textBox) return;
                p.textBox.set({
                    text: text || 'Tape ton texte...',
                    fill: '#000000'
                });
                p.canvas.renderAll();
            });
        }

        function updateTextSize(size) {
            state.textSize = size;
            textSizeValue.textContent = size + 'px';
            eachPane(function(p) {
                if (!p.textBox) return;
                p.textBox.set({ fontSize: parseInt(size) });
                p.canvas.renderAll();
            });
        }

        function updateLineHeight(value) {
            // value est en pourcentage (80-200), on le convertit en ratio (0.8-2.0)
            const ratio = value / 100;
            state.lineHeight = ratio;
            lineHeightValue.textContent = ratio.toFixed(1);
            eachPane(function(p) {
                if (!p.textBox) return;
                p.textBox.set({ lineHeight: ratio });
                p.canvas.renderAll();
            });
        }

        // ============================================
        // OVERLAY TEXT
        // ============================================
        function toggleOverlay() {
            state.showOverlay = !state.showOverlay;
            overlaySwitch.classList.toggle('active', state.showOverlay);
            overlayTextInput.style.display = state.showOverlay ? 'block' : 'none';

            eachPane(function(p) {
                if (state.showOverlay && state.overlayText) {
                    addOverlayText(p);
                } else if (p.overlayTextObj) {
                    p.canvas.remove(p.overlayTextObj);
                    p.overlayTextObj = null;
                }
                p.canvas.renderAll();
            });
        }

        function addOverlayText(p) {
            if (p.overlayTextObj) {
                p.canvas.remove(p.overlayTextObj);
            }

            const template = templateOf(p);
            const frame = template.frame;
            const offset = CANVAS_PADDING;

            p.overlayTextObj = new fabric.Text(state.overlayText.toUpperCase(), {
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

            p.canvas.add(p.overlayTextObj);
            p.canvas.bringToFront(p.overlayTextObj);
            p.canvas.bringToFront(p.watermark);
            p.canvas.renderAll();
        }

        function addOverlayTextToAllPanes() {
            eachPane(function(p) { addOverlayText(p); });
        }

        function updateOverlayText(text) {
            state.overlayText = text;
            eachPane(function(p) {
                if (state.showOverlay && text) {
                    addOverlayText(p);
                } else if (p.overlayTextObj && !text) {
                    p.canvas.remove(p.overlayTextObj);
                    p.overlayTextObj = null;
                    p.canvas.renderAll();
                }
            });
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
            eachPane(function(p) {
                if (!p.imageObj) return;

                const template = templateOf(p);
                const frame = template.frame;
                const offset = CANVAS_PADDING;

                applyCropToImage(p.imageObj, state.cropRatio);

                const quarterTurn = (state.rotation % 180) !== 0;
                const srcW = quarterTurn ? p.imageObj.height : p.imageObj.width;
                const srcH = quarterTurn ? p.imageObj.width : p.imageObj.height;

                const baseScale = Math.max(frame.width / srcW, frame.height / srcH);
                p.imageObj._baseScale = baseScale;

                const finalScale = baseScale * (p.imageScale / 100);
                const centerX = frame.x + frame.width / 2 + offset;
                const centerY = frame.y + frame.height / 2 + offset;

                p.imageObj.set({
                    angle: state.rotation,
                    flipX: state.flipX,
                    flipY: state.flipY,
                    scaleX: finalScale,
                    scaleY: finalScale,
                    left: centerX + p.imageOffsetX,
                    top: centerY + p.imageOffsetY
                });
                p.imageObj.setCoords();
                p.canvas.requestRenderAll();
            });
            updateImageEditReadouts();
        }

        // Les filtres sont recalculés sur toute l'image : sur un slider
        // qui émet à chaque pixel de course, on ne garde que la dernière
        // valeur de chaque frame.
        let filterFrame = null;

        function applyImageFiltersNow() {
            filterFrame = null;
            let filterFailureNoted = false;

            eachPane(function(p) {
                if (!p.imageObj) return;

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

                p.imageObj.filters = filters;
                try {
                    p.imageObj.applyFilters();
                } catch (err) {
                    // Un échec de filtre est VISIBLE : on revient à l'image
                    // nue et on le dit, au lieu de laisser un canvas figé
                    // sans explication. Un seul message pour les deux plateaux.
                    console.error('[editor] application des filtres impossible', err);
                    if (!filterFailureNoted) {
                        filterFailureNoted = true;
                        note('Les réglages n’ont pas pu être appliqués à cette image. L’image d’origine est conservée.', 'error');
                    }
                    p.imageObj.filters = [];
                    try { p.imageObj.applyFilters(); } catch (e) { /* déjà signalé */ }
                }
                p.canvas.requestRenderAll();
            });
        }

        function applyImageFilters() {
            if (!panes.ig.imageObj && !panes.tt.imageObj) return;
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

        /** Dimensions annoncées : source, fenêtre de recadrage, rotation.
            La SOURCE est la même pour les deux plateaux : on lit le premier
            objet image disponible. */
        function updateImageEditReadouts() {
            if (!cropReadout || !orientReadout) return;

            const imageObj = panes.ig.imageObj || panes.tt.imageObj;
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
            if (panes.ig.imageObj || panes.tt.imageObj) {
                reapplyImageTransforms();
                applyImageFilters();
            }
            if (!silent) note('Retouche annulée : recadrage, rotation et réglages remis à zéro.', 'success');
        }

        // ============================================
        // FICHIER DE SORTIE (LOT C)
        // ============================================

        /** Dimensions réelles du fichier produit PAR PLATEAU, multiplicateur compris. */
        function exportPixelSize(p) {
            const template = templateOf(p);
            return {
                width: Math.round(template.width * state.exportScale),
                height: Math.round(template.height * state.exportScale)
            };
        }

        function updateExportReadout() {
            if (!exportDims) return;
            // Deux fichiers sortent désormais : on annonce les deux tailles.
            const parts = activePanes().map(function(p) {
                const size = exportPixelSize(p);
                return `${p.label} ${size.width}×${size.height}`;
            });
            exportDims.textContent = parts.length
                ? parts.join(' + ') + ' px'
                : 'aucun plateau actif';
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
        function renderCanvasToDataURL(p, format, quality, multiplier) {
            // Ce que l'utilisateur voit doit être ce qui sort du canvas :
            // on vide d'abord les réglages en attente.
            flushImageFilters();

            const template = templateOf(p);
            const originalZoom = p.canvas.getZoom();

            p.frameBorder.set({ visible: false });
            p.frameRect.set({ visible: false });
            // Les tiers sont un repère d'écran : `excludeFromExport` les
            // retire déjà, on les masque en plus — un export ne doit
            // JAMAIS dépendre d'un seul garde-fou.
            const thirdsWereVisible = !!(p.thirdsLines && p.thirdsLines.length
                && p.thirdsLines[0].visible);
            if (thirdsWereVisible) p.thirdsLines.forEach(function(l) { l.set({ visible: false }); });
            // Le placeholder « Tape ton texte... » est une AIDE d'édition, pas
            // du contenu : sans cette garde il était GRAVÉ dans chaque export
            // dont le bandeau était vide — et la planification double l'écrivait
            // dans les deux vignettes du calendrier d'un coup.
            const placeholderVisible = p.textBox
                && p.textBox.visible !== false
                && p.textBox.text === 'Tape ton texte...';
            if (placeholderVisible) p.textBox.set({ visible: false });
            p.canvas.discardActiveObject();
            // Transform identité et pas setZoom(1) : sous 900px le viewport
            // porte une translation (cadrage mobile) que setZoom conserverait
            // — l'export sortirait décalé. Sur desktop, strictement équivalent.
            p.canvas.setViewportTransform([1, 0, 0, 1, 0, 0]);
            p.canvas.setWidth(template.width + (CANVAS_PADDING * 2));
            p.canvas.setHeight(template.height + (CANVAS_PADDING * 2));
            p.canvas.renderAll();

            try {
                return p.canvas.toDataURL({
                    format: format,
                    quality: quality,
                    multiplier: multiplier || 1,
                    left: CANVAS_PADDING,
                    top: CANVAS_PADDING,
                    width: template.width,
                    height: template.height
                });
            } finally {
                p.canvas.setZoom(originalZoom);
                updateCanvasSize(p);
                p.frameBorder.set({ visible: true });
                p.frameRect.set({ visible: true });
                if (thirdsWereVisible) p.thirdsLines.forEach(function(l) { l.set({ visible: true }); });
                if (placeholderVisible) p.textBox.set({ visible: true });
                p.canvas.renderAll();
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
            eachPane(function(p) {
                p.imageOffsetX = 0;
                p.imageOffsetY = 0;
                p.imageScale = 100;
            });

            imageScaleSlider.value = 100;
            imageScaleValue.textContent = '100%';

            // LOT C — « Réinitialiser » repart d'une composition vierge :
            // la retouche de l'image en fait partie.
            Object.assign(state, IMAGE_EDIT_DEFAULTS);
            syncImageEditControls();

            eachPane(function(p) { createElements(p); });

            if (state.imageSrc) {
                addImageToAllPanes(state.imageSrc);
            }
            if (state.showOverlay && state.overlayText) {
                addOverlayTextToAllPanes();
            }
            updateText(state.text);
            updateTextSize(state.textSize);
            // Le parcours relit l'état qu'il affiche : afficheurs des
            // steppers et étiquettes des cartes d'export.
            if (typeof syncStepperReadouts === 'function') syncStepperReadouts();
            if (typeof syncExportCards === 'function') syncExportCards();
        }

        function exportMeme() {
            if (state.mediaType === 'video') {
                exportVideo();
            } else {
                exportImage();
            }
        }

        // ============================================
        // PLANIFICATION DOUBLE
        // --------------------------------------------
        // « Planifier » propose UN post par plateau actif — Instagram avec
        // l'export du canvas gauche, TikTok avec celui du canvas droit —
        // même date/heure proposée, modifiable avant validation. Le POST
        // /api/calendar/posts existant est réutilisé tel quel : la colonne
        // `platforms` (JSON) porte la plateforme, aucune migration.
        // ============================================

        /** Valeur `datetime-local` à partir d'un Date (fuseau local). */
        function toDatetimeLocalValue(d) {
            const pad = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
                + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
        }

        function schedulePost() {
            if (!scheduleDialog || typeof scheduleDialog.showModal !== 'function') {
                note('Le dialogue de planification est indisponible dans ce navigateur.', 'error');
                return;
            }

            // Même date/heure PROPOSÉE pour les deux posts : prochaine
            // heure ronde, au moins 30 minutes devant.
            const proposed = new Date(Date.now() + 90 * 60 * 1000);
            proposed.setMinutes(0, 0, 0);
            scheduleDatetime.value = toDatetimeLocalValue(proposed);

            // Un plateau désactivé n'est pas proposé (mais reste décochable
            // → recochable tant qu'il est actif).
            scheduleCheckIG.checked = panes.ig.enabled;
            scheduleCheckIG.disabled = !panes.ig.enabled;
            scheduleCheckTT.checked = panes.tt.enabled;
            scheduleCheckTT.disabled = !panes.tt.enabled;
            if (scheduleIGDims) {
                const t = templateOf(panes.ig);
                scheduleIGDims.textContent = `${t.width}×${t.height}`;
            }

            scheduleDialog.showModal();
        }

        /** Rend la vignette d'UN plateau et crée son post au calendrier. */
        function createCalendarPost(p, scheduledAtTs) {
            let dataURL;
            try {
                dataURL = renderCanvasToDataURL(p, 'jpeg', 0.9, 1);
            } catch (err) {
                console.error('[editor] rendu de la vignette impossible (' + p.label + ')', err);
                return Promise.reject(new Error('rendu ' + p.label));
            }

            const postData = {
                title: 'Meme — ' + p.label,
                caption: state.text || '',
                media_type: state.mediaType === 'video' ? 'video' : 'image',
                template_format: templateKeyOf(p),
                thumbnail: dataURL,  // base64 data URL saved as thumbnail
                status: 'scheduled',
                scheduled_at: scheduledAtTs,
                platforms: JSON.stringify([p.platform]),
            };

            return fetch('/api/calendar/posts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(postData)
            }).then(r => {
                if (!r.ok) throw new Error('Failed to create post (' + p.label + ')');
                return r.json();
            });
        }

        function confirmSchedule() {
            // LOT C — même règle que pour l'export : la vignette envoyée au
            // Calendrier doit porter les réglages déjà visibles à l'écran.
            flushImageFilters();

            const chosen = [];
            if (scheduleCheckIG.checked && panes.ig.enabled) chosen.push(panes.ig);
            if (scheduleCheckTT.checked && panes.tt.enabled) chosen.push(panes.tt);
            if (!chosen.length) {
                note('Choisis au moins une plateforme à planifier.', 'warning');
                return;
            }

            const when = new Date(scheduleDatetime.value);
            if (isNaN(when.getTime())) {
                note('La date de publication est invalide.', 'warning');
                return;
            }
            const ts = Math.round(when.getTime() / 1000);

            scheduleBtn.disabled = true;
            Promise.all(chosen.map(p => createCalendarPost(p, ts)))
                .then(() => {
                    window.location.href = '/calendar';
                })
                .catch(err => {
                    console.error('Schedule error:', err);
                    scheduleBtn.disabled = false;
                    // Promise.all peut avoir créé UN des deux posts avant
                    // l'échec : on le dit, pour éviter un doublon au retry.
                    note('La planification a échoué (' + err.message + '). Vérifie le Calendrier avant de réessayer : un des posts peut déjà exister.', 'error');
                });
        }

        function saveMemeToViewer() {
            if (state.mediaType === 'video') {
                // LOT A — la vidéo passe par FFmpeg côté serveur : un MP4 par
                // plateau actif, rendus SÉQUENTIELLEMENT, tous deux dans le Viewer.
                saveVideoMemesToViewer();
                return;
            }

            const targets = activePanes();
            if (!targets.length) {
                note('Aucun plateau actif : réactive Instagram ou TikTok pour sauvegarder.', 'warning');
                return;
            }

            // LOT C — la taille cible s'applique aussi à la copie Viewer.
            // Le format reste PNG : /api/viewer/memes écrit toujours un
            // fichier .png et le sert en image/png — y déposer du JPEG
            // produirait un fichier mal nommé et mal typé.
            // ÉDITEUR DOUBLE : une copie PAR plateau actif.
            const jobs = [];
            for (const p of targets) {
                const size = exportPixelSize(p);
                let dataURL;
                try {
                    dataURL = renderCanvasToDataURL(p, 'png', 1, state.exportScale);
                } catch (err) {
                    console.error('[editor] rendu du meme impossible (' + p.label + ')', err);
                    note('Le meme ' + p.label + ' n’a pas pu être rendu. Réduis la taille cible et réessaie.', 'error');
                    return;
                }
                if (!dataURL || dataURL.length < 100) {
                    note('Le meme ' + p.label + ' rendu est vide. Réduis la taille cible et réessaie.', 'error');
                    return;
                }
                jobs.push({ pane: p, size: size, dataURL: dataURL });
            }

            // Save to backend
            if (saveMemeBtn) {
                saveMemeBtn.disabled = true;
                saveMemeBtn.textContent = '⏳ Sauvegarde...';
            }

            Promise.all(jobs.map(job => fetch('/api/viewer/memes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    image_data: job.dataURL,
                    title: 'Meme — ' + job.pane.label,
                    caption: state.text || '',
                    template_format: templateKeyOf(job.pane),
                    media_type: 'image',
                })
            }).then(r => {
                if (!r.ok) throw new Error('Failed to save meme (' + job.pane.label + ')');
                return r.json();
            })))
            .then(() => {
                if (saveMemeBtn) {
                    saveMemeBtn.disabled = false;
                    saveMemeBtn.textContent = '✅ Sauvegarde !';
                    setTimeout(() => {
                        saveMemeBtn.textContent = 'Sauvegarder dans Médias';
                    }, 2000);
                }
                // LOT C — dimensions réellement enregistrées, pas un « OK » nu.
                const detail = jobs.map(j => `${j.pane.label} ${j.size.width}×${j.size.height}`).join(', ');
                note(`Meme${jobs.length > 1 ? 's' : ''} enregistré${jobs.length > 1 ? 's' : ''} dans le Viewer : PNG ${detail} px.`, 'success');
            })
            .catch(err => {
                console.error('Save meme error:', err);
                // Promise.all : une des deux copies peut déjà exister.
                note('La sauvegarde a échoué (' + err.message + '). Vérifie le Viewer avant de réessayer, puis télécharge les fichiers pour ne rien perdre.', 'error');
                if (saveMemeBtn) {
                    saveMemeBtn.disabled = false;
                    saveMemeBtn.textContent = 'Sauvegarder dans Médias';
                }
            });
        }

        // ============================================
        // LOT A — SAUVEGARDE VIDÉO DANS LE VIEWER
        // --------------------------------------------
        // Un MP4 PAR plateau actif (Instagram au format choisi, TikTok
        // 1080×1920 plein cadre avec POV et filigrane incrustés), rendus
        // SÉQUENTIELLEMENT côté serveur — jamais deux ffmpeg concurrents,
        // l'audit a montré la corruption d'écritures concurrentes. Chaque
        // vidéo est sauvegardée dès que SON rendu aboutit : un échec sur la
        // 2e ne perd pas la 1re, et le message le dit.
        // ============================================
        async function saveVideoMemesToViewer() {
            const targets = activePanes();
            if (!targets.length) {
                note('Aucun plateau actif : réactive Instagram ou TikTok pour sauvegarder.', 'warning');
                return;
            }
            if (!state.videoFile) {
                note('Aucun fichier vidéo en mémoire : réimporte la vidéo puis réessaie.', 'error');
                return;
            }

            // Modal de progression — mêmes ids que le modal d'export pour
            // réutiliser updateProgress(). Un seul modal à la fois.
            const existing = document.getElementById('export-modal');
            if (existing) existing.remove();
            const modal = document.createElement('div');
            modal.id = 'export-modal';
            modal.style.cssText = 'position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.9); display: flex; align-items: center; justify-content: center; z-index: 1000;';
            modal.innerHTML = `
                <div style="background: #1a1a1a; border-radius: 12px; padding: 32px; max-width: 500px; text-align: center;">
                    <div style="font-size: 48px; margin-bottom: 16px; animation: spin 1s linear infinite;">⏳</div>
                    <style>@keyframes spin { to { transform: rotate(360deg); } }</style>
                    <h2 style="margin-bottom: 16px; color: #fff;">Sauvegarde dans le Viewer</h2>
                    <p style="color: #888; margin-bottom: 24px;">
                        ${targets.length} vidéo${targets.length > 1 ? 's' : ''} à rendre (FFmpeg), l'une après l'autre.
                    </p>
                    <div id="progress-bar" style="background: #333; border-radius: 4px; height: 8px; overflow: hidden; margin-bottom: 16px;">
                        <div id="progress-fill" style="background: #ef4444; height: 100%; width: 0%; transition: width 0.3s;"></div>
                    </div>
                    <p id="progress-text" style="color: #666; font-size: 12px;">Préparation...</p>
                </div>
            `;
            document.body.appendChild(modal);

            if (saveMemeBtn) {
                saveMemeBtn.disabled = true;
                saveMemeBtn.textContent = '⏳ Sauvegarde...';
            }

            const saved = [];
            try {
                for (let i = 0; i < targets.length; i++) {
                    const p = targets[i];
                    // Progression HONNÊTE : chaque vidéo occupe sa part de la
                    // barre, et le libellé dit laquelle est en cours.
                    const step = 'vidéo ' + (i + 1) + '/' + targets.length + ' (' + p.label + ')';
                    const base = (i / targets.length) * 100;
                    const span = 100 / targets.length;

                    updateProgress(Math.round(base + span * 0.05), step + ' — génération du template…');
                    const params = buildVideoExportParams(p);
                    const templateBlob = await generateTemplatePNG(params);

                    updateProgress(Math.round(base + span * 0.25), step + ' — upload et rendu FFmpeg…');
                    const formData = new FormData();
                    formData.append('video', state.videoFile);
                    formData.append('template', templateBlob, 'template.png');
                    formData.append('params', JSON.stringify(ffmpegParamsOf(params)));
                    // Suffixe -instagram / -tiktok du fichier sauvegardé.
                    formData.append('platform', p.platform);
                    formData.append('title', 'Meme vidéo — ' + p.label);
                    formData.append('caption', state.text || '');
                    formData.append('template_format', templateKeyOf(p));

                    const response = await fetch('/api/editor/save-video-meme', {
                        method: 'POST',
                        body: formData
                    });
                    if (!response.ok) {
                        let message = 'Erreur serveur';
                        try {
                            const payload = await response.json();
                            if (payload && payload.error) message = payload.error;
                        } catch (e) { /* réponse non-JSON : on garde le message générique */ }
                        throw new Error(message);
                    }
                    saved.push(p.label);
                    updateProgress(Math.round(base + span), step + ' — sauvegardée ✅');
                }

                updateProgress(100, 'Terminé !');
                modal.querySelector('div > div').innerHTML = `
                    <div style="font-size: 48px; margin-bottom: 16px;">✅</div>
                    <h2 style="margin-bottom: 16px; color: #fff;">Sauvegarde réussie !</h2>
                    <p style="color: #888; margin-bottom: 24px;">
                        ${saved.length} vidéo${saved.length > 1 ? 's' : ''} (${saved.join(' + ')}) ajoutée${saved.length > 1 ? 's' : ''} aux memes du Viewer.
                    </p>
                    <button onclick="document.getElementById('export-modal').remove()"
                            style="padding: 12px 32px; background: #ef4444; border: none; border-radius: 8px; color: #fff; cursor: pointer; font-weight: 600;">
                        Fermer
                    </button>
                `;
                note('Vidéo' + (saved.length > 1 ? 's' : '') + ' sauvegardée' + (saved.length > 1 ? 's' : '') + ' dans le Viewer : ' + saved.join(' + ') + '.', 'success');
                if (saveMemeBtn) {
                    saveMemeBtn.disabled = false;
                    saveMemeBtn.textContent = '✅ Sauvegarde !';
                    setTimeout(() => {
                        saveMemeBtn.textContent = 'Sauvegarder dans Médias';
                    }, 2000);
                }
            } catch (err) {
                console.error('[editor] sauvegarde vidéo Viewer', err);
                // HONNÊTETÉ : ce qui est déjà sauvegardé est acquis — chaque
                // rendu réussi vit déjà dans le Viewer, seul le raté manque.
                const acquis = saved.length
                    ? 'La vidéo ' + saved.join(' + ') + ' est DÉJÀ dans le Viewer — elle n\'est pas perdue. Seule la suivante a échoué.'
                    : 'Aucune vidéo n\'a été sauvegardée.';
                modal.querySelector('div > div').innerHTML = `
                    <div style="font-size: 48px; margin-bottom: 16px;">❌</div>
                    <h2 style="margin-bottom: 16px; color: #fff;">Erreur</h2>
                    <p style="color: #ef4444; margin-bottom: 12px;">${err.message}</p>
                    <p style="color: #888; margin-bottom: 24px;">${acquis}</p>
                    <button onclick="document.getElementById('export-modal').remove()"
                            style="padding: 12px 32px; background: #333; border: none; border-radius: 8px; color: #fff; cursor: pointer;">
                        Fermer
                    </button>
                `;
                note('La sauvegarde vidéo a échoué (' + err.message + '). ' + acquis, 'error');
                if (saveMemeBtn) {
                    saveMemeBtn.disabled = false;
                    saveMemeBtn.textContent = 'Sauvegarder dans Médias';
                }
            }
        }

        function exportImage() {
            // LOT C — format (PNG/JPEG), qualité et taille cible viennent
            // du bloc « Fichier de sortie ». Tout est rendu par le canvas :
            // pas d'upload, pas de fichier temporaire, retour immédiat.
            // ÉDITEUR DOUBLE : un fichier PAR plateau actif.
            const targets = activePanes();
            if (!targets.length) {
                note('Aucun plateau actif : réactive Instagram ou TikTok pour exporter.', 'warning');
                return;
            }

            const isJpeg = state.exportFormat === 'jpeg';
            const timestamp = new Date().toISOString().slice(0, 10);
            const produced = [];

            for (const p of targets) {
                const size = exportPixelSize(p);
                let dataURL;
                try {
                    dataURL = renderCanvasToDataURL(
                        p,
                        isJpeg ? 'jpeg' : 'png',
                        isJpeg ? state.exportQuality / 100 : 1,
                        state.exportScale
                    );
                } catch (err) {
                    // Un export raté ne disparaît pas en silence : il se dit.
                    console.error('[editor] export image impossible (' + p.label + ')', err);
                    note('L’image ' + p.label + ' n’a pas pu être produite. Réduis la taille cible et réessaie.', 'error');
                    return;
                }

                if (!dataURL || dataURL.length < 100) {
                    note('L’image ' + p.label + ' produite est vide. Réduis la taille cible et réessaie.', 'error');
                    return;
                }

                const link = document.createElement('a');
                link.download = `samourais_meme_${p.platform}_${size.width}x${size.height}_${timestamp}.${isJpeg ? 'jpg' : 'png'}`;
                link.href = dataURL;
                link.click();
                produced.push(`${p.label} ${size.width}×${size.height} px (${formatBytes(dataURLBytes(dataURL))})`);
            }

            note(`Téléchargé : ${produced.join(' + ')}, ${isJpeg ? 'JPEG ' + state.exportQuality + '%' : 'PNG'}.`, 'success');
        }

        /** Paramètres d'export vidéo d'UN plateau — partagés entre le
         *  téléchargement (exportVideo) et la sauvegarde Viewer (LOT A). */
        function buildVideoExportParams(p) {
            const template = templateOf(p);

            // Calculate effective frame height and centered Y position
            const effectiveFrameHeight = Math.round(template.frame.height * (p.frameHeightPercent / 100));
            const originalCenterY = template.frame.y + template.frame.height / 2;
            const effectiveFrameY = originalCenterY - effectiveFrameHeight / 2;

            return {
                // Plateau source (sert au rendu du template PNG)
                paneKey: p.key,
                // Template info
                template: templateKeyOf(p),
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
                // Media position/scale — le cadrage du plateau exporté
                imageScale: p.imageScale,
                imageOffsetX: p.imageOffsetX,
                imageOffsetY: p.imageOffsetY,
                // Text
                text: state.text,
                textSize: state.textSize,
                lineHeight: state.lineHeight,
                textX: p.textBox ? p.textBox.left - CANVAS_PADDING : template.textArea.x,
                textY: p.textBox ? p.textBox.top - CANVAS_PADDING : template.textArea.y,
                // Overlay
                overlayText: state.showOverlay ? state.overlayText : '',
                // Watermark position and opacity
                watermarkX: p.watermark ? p.watermark.left - CANVAS_PADDING : template.watermark.x,
                watermarkY: p.watermark ? p.watermark.top - CANVAS_PADDING : template.watermark.y,
                watermarkOpacity: state.watermarkOpacity,
            };
        }

        /** Sous-ensemble des paramètres réellement consommés par FFmpeg côté
         *  serveur — même contrat pour /process-video et /save-video-meme. */
        function ffmpegParamsOf(params) {
            return {
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
        }

        async function exportVideo() {
            // Le TÉLÉCHARGEMENT vidéo reste UN SEUL fichier MP4 : il suit le
            // premier plateau actif (Instagram si les deux le sont). Le
            // pipeline FFmpeg est inchangé — seule la source des paramètres
            // change. La sortie DOUBLE, elle, passe par « Sauvegarder dans
            // Viewer » (saveVideoMemesToViewer).
            const p = activePanes()[0] || panes.ig;
            const exportParams = buildVideoExportParams(p);

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
                    logoInk = measureLogoInk(img.getElement());
                    // Le PNG vient d'arriver : chaque plateau déjà
                    // construit remplace son repli texte par le logo,
                    // à la MÊME géométrie (placeWatermark).
                    eachPane(function(p) {
                        if (!p.canvas || !p.watermark) return;
                        buildWatermark(p);
                        p.canvas.bringToFront(p.watermark);
                        p.canvas.renderAll();
                    });
                }
            }, { crossOrigin: 'anonymous' });
        }

        // ============================================
        // TEMPLATE PNG PLEIN ÉCRAN (plateau TikTok)
        // --------------------------------------------
        // Pas de gabarit blanc : la surcouche envoyée à FFmpeg est le rendu
        // EXACT du canvas TikTok (POV, texte overlay, filigrane) sur fond
        // transparent — donc ce que l'utilisateur voit à l'écran, y compris
        // le bloc-par-ligne Montserrat que l'ancien chemin ne savait pas
        // dessiner. Là où le média ne couvre pas (dézoom volontaire), le
        // fond est le même NOIR que le plateau — jamais le blanc que le
        // pipeline FFmpeg peint sous la vidéo.
        // ============================================
        async function generateFullBleedTemplatePNG(p) {
            const template = templateOf(p);
            await document.fonts.ready;

            // 1) Surcouche : le canvas réel, média et fonds masqués, fond
            //    transparent — même mécanique de restauration que
            //    renderCanvasToDataURL, y compris en cas d'échec.
            const hidden = [];
            [p.imageObj, p.templateBg, p.frameRect, p.frameBorder].forEach(function(obj) {
                if (obj && obj.visible !== false) {
                    obj.set({ visible: false });
                    hidden.push(obj);
                }
            });
            const originalZoom = p.canvas.getZoom();
            const originalBg = p.canvas.backgroundColor;
            p.canvas.discardActiveObject();
            p.canvas.backgroundColor = '';
            // Même garde que renderCanvasToDataURL : transform identité, pas
            // setZoom(1) — la translation du cadrage mobile fausserait le PNG.
            p.canvas.setViewportTransform([1, 0, 0, 1, 0, 0]);
            p.canvas.setWidth(template.width + (CANVAS_PADDING * 2));
            p.canvas.setHeight(template.height + (CANVAS_PADDING * 2));
            p.canvas.renderAll();
            let overlayURL;
            try {
                overlayURL = p.canvas.toDataURL({
                    format: 'png',
                    left: CANVAS_PADDING,
                    top: CANVAS_PADDING,
                    width: template.width,
                    height: template.height
                });
            } finally {
                hidden.forEach(function(obj) { obj.set({ visible: true }); });
                p.canvas.backgroundColor = originalBg;
                p.canvas.setZoom(originalZoom);
                updateCanvasSize(p);
                p.canvas.renderAll();
            }

            const overlayImg = await new Promise(function(resolve, reject) {
                const img = new Image();
                img.onload = function() { resolve(img); };
                img.onerror = function() { reject(new Error('Surcouche TikTok illisible')); };
                img.src = overlayURL;
            });

            // 2) Fond noir, troué à l'emplacement EXACT où FFmpeg posera la
            //    vidéo (même calcul « cover » que le canvas et que le serveur :
            //    scale = max(1080/w, 1920/h) × zoom, centre + décalage).
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = template.width;
            tempCanvas.height = template.height;
            const ctx = tempCanvas.getContext('2d');

            ctx.fillStyle = '#000000';
            ctx.fillRect(0, 0, template.width, template.height);

            ctx.globalCompositeOperation = 'destination-out';
            const vidW = videoSource.videoWidth;
            const vidH = videoSource.videoHeight;
            if (vidW && vidH) {
                const baseScale = Math.max(template.width / vidW, template.height / vidH);
                const s = baseScale * ((p.imageScale || 100) / 100);
                // +2 px : absorbe les arrondis FFmpeg, un liseré noir d'un
                // demi-pixel se verrait sur un plein écran.
                const w = vidW * s + 2;
                const h = vidH * s + 2;
                const cx = template.width / 2 + (p.imageOffsetX || 0);
                const cy = template.height / 2 + (p.imageOffsetY || 0);
                ctx.fillRect(cx - w / 2, cy - h / 2, w, h);
            } else {
                // Dimensions vidéo inconnues : trou plein cadre (cas nominal,
                // le média couvre tout de toute façon).
                ctx.fillRect(0, 0, template.width, template.height);
            }
            ctx.globalCompositeOperation = 'source-over';

            // 3) La surcouche PAR-DESSUS le trou : POV, overlay, filigrane.
            ctx.drawImage(overlayImg, 0, 0);

            return new Promise(function(resolve) {
                tempCanvas.toBlob(function(blob) { resolve(blob); }, 'image/png');
            });
        }

        // Générer le template PNG avec trou transparent pour la vidéo
        async function generateTemplatePNG(params) {
            // Le plateau source est celui choisi par exportVideo().
            const p = panes[params.paneKey] || panes.ig;
            // Plateau plein écran (TikTok) : pas de gabarit blanc — chemin
            // dédié, le reste de cette fonction est celui du gabarit meme.
            if (isFullBleed(p)) return generateFullBleedTemplatePNG(p);
            const template = TEMPLATES[params.template] || templateOf(p);
            const textBox = p.textBox;
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
            const watermark = p.watermark;
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
                formData.append('params', JSON.stringify(ffmpegParamsOf(params)));
                
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
                link.download = `samourais_meme_${params.template}_${Date.now()}.mp4`;
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
            // Format selection — NE PILOTE QUE le plateau Instagram :
            // le plateau TikTok est fixe en 1080×1920.
            formatBtns.forEach(btn => {
                btn.addEventListener('click', () => {
                    formatBtns.forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    state.currentTemplate = btn.dataset.format;

                    const ig = panes.ig;
                    ig.imageOffsetX = 0;
                    ig.imageOffsetY = 0;
                    ig.imageScale = 100;
                    ig.frameHeightPercent = 100;

                    imageScaleSlider.value = 100;
                    imageScaleValue.textContent = '100%';
                    frameHeightSlider.value = 100;
                    frameHeightValue.textContent = '100%';

                    // Frame height slider available for all formats (1:1, 4:5, 9:16)
                    frameHeightSection.style.display = 'block';

                    // LOT C — les dimensions annoncées du fichier de sortie
                    // suivent le format choisi.
                    updateExportReadout();
                    if (typeof syncExportCards === 'function') syncExportCards();
                    if (typeof syncStepperReadouts === 'function') syncStepperReadouts();

                    // Le libellé du plateau Instagram suit le format.
                    if (stageDimsIG) {
                        const t = templateOf(ig);
                        stageDimsIG.textContent = `${t.width}×${t.height}`;
                    }

                    updateCanvasSize(ig);
                    createElements(ig);

                    if (state.imageSrc) {
                        addImageToCanvas(ig, state.imageSrc);
                    }
                    if (state.showOverlay && state.overlayText) {
                        addOverlayText(ig);
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

            // Select image button — sélectionne le média sur chaque plateau
            // actif (deux canvas indépendants, deux sélections).
            selectImageBtn.addEventListener('click', () => {
                activePanes().forEach(function(p) {
                    if (p.imageObj) {
                        p.canvas.setActiveObject(p.imageObj);
                        p.canvas.renderAll();
                    }
                });
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
                    eachPane(function(p) {
                        if (p.imageObj) { p.imageObj.set({ flipX: state.flipX }); p.canvas.requestRenderAll(); }
                    });
                    updateImageEditReadouts();
                });
            }
            if (flipVBtn) {
                flipVBtn.addEventListener('click', () => {
                    state.flipY = !state.flipY;
                    flipVBtn.setAttribute('aria-pressed', String(state.flipY));
                    eachPane(function(p) {
                        if (p.imageObj) { p.imageObj.set({ flipY: state.flipY }); p.canvas.requestRenderAll(); }
                    });
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
            window.addEventListener('resize', updateAllCanvasSizes);

            // ---- Interrupteurs de plateau (éditeur double) ----
            eachPane(function(p) {
                const btn = document.getElementById('toggle-' + p.key);
                if (!btn) return;
                btn.addEventListener('click', function() {
                    p.enabled = !p.enabled;
                    btn.setAttribute('aria-pressed', String(p.enabled));
                    const sw = btn.querySelector('.toggle-switch');
                    if (sw) sw.classList.toggle('active', p.enabled);
                    const stage = document.getElementById(p.stageId);
                    if (stage) stage.classList.toggle('is-off', !p.enabled);
                    // L'autre plateau récupère la place libérée.
                    updateAllCanvasSizes();
                    updateExportReadout();
                });
            });

            // ---- Texte TikTok « POV » ----
            if (povTextInput) {
                povTextInput.addEventListener('input', function(e) {
                    state.povText = e.target.value;
                    ensurePovObject();
                });
            }
            if (povStyleGroup) {
                povStyleGroup.addEventListener('click', function(e) {
                    const btn = e.target.closest('.seg__btn');
                    if (!btn) return;
                    // Le style 'outline' (contour noir) a été ajouté au
                    // groupe SANS être ajouté ici : ce ternaire renvoyait
                    // 'light' pour tout ce qui n'était pas 'dark', donc le
                    // bouton « Contour noir » posait un fond blanc. Le
                    // défaut de `state` étant 'outline', le style natif
                    // était correct au chargement et devenait INATTEIGNABLE
                    // dès le premier clic dans ce groupe.
                    state.povStyle = POV_STYLES[btn.dataset.povstyle] ? btn.dataset.povstyle : 'outline';
                    povStyleGroup.querySelectorAll('.seg__btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    restylePovObject();
                });
            }

            // ---- Dialogue de planification double ----
            if (scheduleForm) {
                scheduleForm.addEventListener('submit', function(e) {
                    // `method="dialog"` ferme la boîte quel que soit le
                    // bouton ; on ne crée les posts que sur « confirm ».
                    if (e.submitter && e.submitter.value === 'confirm') {
                        confirmSchedule();
                    }
                });
            }
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
                    el.innerHTML = `<img src="${item.file_url || item.media_url || ''}" alt="${item.caption || ''}" loading="lazy">`
                        + '<span class="drive-file__check" aria-hidden="true">\u2713</span>';
                    el.onclick = () => loadLibraryItem(item);
                    // Champ de recherche : filtre CLIENT sur ce que la
                    // vignette montre déjà (légende, plateforme, compte).
                    // /api/viewer/media n'accepte pas de terme de recherche
                    // et lui en inventer un sortirait de « uniquement
                    // l'interface ».
                    el.dataset.mediaId = String(item.id);
                    el.dataset.search = [item.caption, item.platform, item.profile_username]
                        .filter(Boolean).join(' ').toLowerCase();
                    el.setAttribute('role', 'button');
                    el.setAttribute('tabindex', '0');
                    el.setAttribute('aria-pressed', 'false');
                    el.addEventListener('keydown', function (e) {
                        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.click(); }
                    });
                    grid.appendChild(el);
                });

                // Une grille rechargée (« Actualiser », page suivante) doit
                // retrouver la sélection courante et le filtre courant.
                markLibrarySelection();
                filterLibraryGrid();

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

                // On RESTE dans la bibliothèque : le parcours veut que la
                // vignette choisie garde son anneau et son ✓ sous les yeux.
                // L'ancien code rebasculait sur l'onglet « Ordi », ce qui
                // effaçait toute trace du choix qu'on venait de faire.
                state.libraryItem = item;
                state.phrase = (item.phrase || '').trim();
                state.phraseUsed = false;
                markLibrarySelection();
                updateSelectionInfo();
            } catch (e) {
                console.error('Failed to load library item', e);
                note('Le média n\u2019a pas pu être chargé. Vérifie le fichier, puis réessaie.', 'error');
            }
        }

        // Check for media_id in URL params (from viewer "Edit" button)
        function checkMediaParam() {
            const params = new URLSearchParams(window.location.search);
            const mediaId = params.get('media_id');
            if (!mediaId) return;
            // La fiche complète AVANT le chargement : elle porte la
            // `phrase` du Tri rapide, la plateforme et les dimensions —
            // sans elle, un média ouvert depuis la galerie arrivait à
            // l'étape Texte sans sa phrase, alors que le même média
            // choisi dans la grille l'apportait. Un 404 ou une panne
            // réseau ne bloque pas : on charge le média sans sa fiche.
            fetch('/api/viewer/media/' + encodeURIComponent(mediaId))
                .then(function (r) { return r.ok ? r.json() : null; })
                .catch(function () { return null; })
                .then(function (item) {
                    loadLibraryItem(item && item.id
                        ? item
                        : { id: parseInt(mediaId, 10), media_type: 'image' });
                });
        }

        // ============================================
        // TEXTE TIKTOK « POV »
        // --------------------------------------------
        // La typo du texte TikTok (Proxima Nova SemiBold) est COMMERCIALE :
        // on sert Montserrat SemiBold (OFL), vendorisée dans
        // /static/vendor/fonts/ avec sa licence OFL.txt. Le style natif
        // TikTok est un bloc arrondi PAR LIGNE avec un léger débord — Fabric
        // ne le fait pas nativement (textBackgroundColor = rectangles secs),
        // on surcharge donc _renderTextLinesBackground sur L'INSTANCE.
        //
        // PIÈGE CANVAS : Fabric mesure le texte avec la police DISPONIBLE au
        // moment du dessin. Si Montserrat n'est pas encore chargée, il dessine
        // en police de repli SANS ERREUR et fige de mauvaises métriques. D'où
        // l'attente explicite de document.fonts (FontFaceSet.load) avant la
        // création du bloc, et une re-mesure après chargement.
        // ============================================
        const POV_FONT_SPEC = '600 48px Montserrat';
        // Les trois styles natifs de TikTok. `bg: null` = aucun bloc de fond :
        // `povRenderLinesBackground` sort immédiatement, seul le contour reste.
        //
        // PIÈGE FABRIC pour le style « contour » : sans `paintFirst: 'stroke'`,
        // le contour est peint PAR-DESSUS le remplissage et ronge l'intérieur
        // des lettres — le texte paraît maigre et sale. En le peignant d'abord,
        // le contour reste derrière et épaissit la lettre vers l'extérieur,
        // comme le fait TikTok.
        const POV_STYLES = {
            light:   { bg: '#ffffff', fill: '#000000', stroke: null },
            dark:    { bg: 'rgba(0, 0, 0, 0.65)', fill: '#ffffff', stroke: null },
            outline: { bg: null, fill: '#ffffff', stroke: '#000000' }
        };

        //: Épaisseur du contour, PROPORTIONNELLE au corps : un contour fixe
        //: disparaît sur un grand texte et noie un petit.
        const POV_STROKE_RATIO = 0.13;

        /** Propriétés de contour à appliquer (ou à retirer) selon le style. */
        function povStrokeProps(style, fontSize) {
            if (!style.stroke) {
                return { stroke: null, strokeWidth: 0 };
            }
            return {
                stroke: style.stroke,
                strokeWidth: Math.max(2, Math.round(fontSize * POV_STROKE_RATIO)),
                paintFirst: 'stroke',
                strokeLineJoin: 'round',
                strokeLineCap: 'round'
            };
        }
        let povFontReady = false;
        const povFontPromise = (document.fonts && document.fonts.load)
            ? document.fonts.load(POV_FONT_SPEC).then(function(faces) {
                povFontReady = true;
                if (!faces.length) {
                    console.warn('[editor] Montserrat SemiBold introuvable : le POV sortira en police de repli.');
                }
                return faces;
            }).catch(function(err) {
                povFontReady = true; // on ne bloque pas l'éditeur pour autant
                console.error('[editor] chargement de Montserrat impossible', err);
                return [];
            })
            : Promise.resolve([]);

        /** Fond arrondi par ligne, façon TikTok (débord horizontal léger). */
        function povRenderLinesBackground(ctx) {
            if (!this.povBg) return;
            const padX = this.fontSize * 0.38;   // débord latéral
            const radius = this.fontSize * 0.30; // coins arrondis
            const leftOffset = this._getLeftOffset();
            let lineTop = this._getTopOffset();

            ctx.save();
            ctx.fillStyle = this.povBg;
            for (let i = 0; i < this._textLines.length; i++) {
                const heightOfLine = this.getHeightOfLine(i);
                const lineWidth = this.getLineWidth(i);
                if (lineWidth > 0) {
                    const x = leftOffset + this._getLineLeftOffset(i) - padX;
                    const w = lineWidth + padX * 2;
                    const h = heightOfLine;
                    const r = Math.min(radius, h / 2, w / 2);
                    ctx.beginPath();
                    ctx.moveTo(x + r, lineTop);
                    ctx.lineTo(x + w - r, lineTop);
                    ctx.quadraticCurveTo(x + w, lineTop, x + w, lineTop + r);
                    ctx.lineTo(x + w, lineTop + h - r);
                    ctx.quadraticCurveTo(x + w, lineTop + h, x + w - r, lineTop + h);
                    ctx.lineTo(x + r, lineTop + h);
                    ctx.quadraticCurveTo(x, lineTop + h, x, lineTop + h - r);
                    ctx.lineTo(x, lineTop + r);
                    ctx.quadraticCurveTo(x, lineTop, x + r, lineTop);
                    ctx.closePath();
                    ctx.fill();
                }
                lineTop += heightOfLine;
            }
            ctx.restore();
        }

        /** Crée/actualise/retire le bloc POV du canvas TikTok. */
        function ensurePovObject() {
            const p = panes.tt;
            if (!p.canvas) return;

            if (!state.povText.trim()) {
                if (p.povObj) {
                    p.canvas.remove(p.povObj);
                    p.povObj = null;
                    p.canvas.renderAll();
                }
                return;
            }

            // PIÈGE CANVAS (voir en-tête de section) : pas de dessin avant
            // que document.fonts ait résolu Montserrat.
            if (!povFontReady) {
                povFontPromise.then(ensurePovObject);
                return;
            }

            const style = POV_STYLES[state.povStyle] || POV_STYLES.light;

            if (p.povObj) {
                p.povObj.set(Object.assign(
                    { text: state.povText, fill: style.fill },
                    povStrokeProps(style, p.povObj.fontSize)
                ));
                p.povObj.povBg = style.bg;
                p.povObj.initDimensions();
                p.canvas.renderAll();
                return;
            }

            const template = templateOf(p); // story 1080×1920
            const obj = new fabric.Textbox(state.povText, {
                left: CANVAS_PADDING + template.width / 2,
                top: CANVAS_PADDING + template.height * 0.28,
                width: template.width * 0.72,
                originX: 'center',
                fontFamily: 'Montserrat',
                fontWeight: 600,
                fontSize: 48,
                lineHeight: 1.35,
                fill: style.fill,
                textAlign: 'center',
                splitByGrapheme: false,
                hasControls: true,
                cornerSize: 16,
                hoverCursor: 'move',
                moveCursor: 'move',
                // Pas d'édition au double-clic : le POV s'écrit dans le champ
                // dédié, comme la légende — un seul point de vérité.
                editable: false
            });
            obj.set(povStrokeProps(style, obj.fontSize));
            obj.povBg = style.bg;
            obj._renderTextLinesBackground = povRenderLinesBackground;

            p.povObj = obj;
            p.canvas.add(obj);
            p.canvas.bringToFront(obj);
            if (p.watermark) p.canvas.bringToFront(p.watermark);
            p.canvas.renderAll();
        }

        /** Bascule fond blanc/texte noir ↔ fond noir translucide/texte blanc. */
        function restylePovObject() {
            const p = panes.tt;
            if (!p.povObj) { ensurePovObject(); return; }
            const style = POV_STYLES[state.povStyle] || POV_STYLES.outline;
            // `povStrokeProps` en plus de la couleur : sans lui, quitter
            // « Contour noir » laissait le contour en place sur un fond
            // blanc, et y revenir ne le redessinait pas.
            p.povObj.set(Object.assign(
                { fill: style.fill, dirty: true },
                povStrokeProps(style, p.povObj.fontSize)
            ));
            p.povObj.povBg = style.bg;
            p.canvas.renderAll();
        }

        // ============================================================
        // LE PARCOURS EN 4 ÉTAPES
        // ------------------------------------------------------------
        // Média → Cadrage → Texte → Export. Cette section ne fabrique
        // RIEN : elle ne fait que révéler, dans l'ordre, des contrôles
        // qui existaient déjà et dont les gestionnaires n'ont pas
        // changé. Un stepper écrit dans le curseur d'origine puis
        // déclenche son évènement `input` — c'est toujours le curseur
        // qui est la source de vérité, jamais le stepper.
        //
        // L'accordéon d'outils qu'elle remplace est supprimé : sous
        // 900px il repliait des groupes ; ici il n'y a plus de groupes,
        // il y a des ÉTAPES, et c'est la CSS qui n'en montre qu'une
        // (`.editor-app[data-step]`).
        // ============================================================

        const WIZ_LABELS = ['Média', 'Cadrage', 'Texte', 'Export'];
        const WIZ_LAST = 4;
        let wizStep = 1;

        const editorApp = document.getElementById('editor-app');
        const wizStepTag = document.getElementById('wiz-step-tag');
        const wizPrevBtn = document.getElementById('wiz-prev');
        const wizNextBtn = document.getElementById('wiz-next');
        const wizBackBtn = document.getElementById('wiz-back');
        const wizSelInfo = document.getElementById('wiz-selinfo');
        const wizPrefillNote = document.getElementById('wiz-prefill-note');
        const librarySearch = document.getElementById('library-search');
        const librarySearchWrap = document.getElementById('library-search-wrap');
        const libraryGrid = document.getElementById('library-grid');
        const textSizeReadout = document.getElementById('text-size-readout');
        const lineHeightReadout = document.getElementById('line-height-readout');
        const exportCards = document.getElementById('export-cards');

        /** Un média est chargé (image OU vidéo) : c'est le seul verrou du parcours. */
        function hasMedia() { return !!(state.imageSrc || state.videoFile); }

        // ---- Bibliothèque : anneau de sélection, recherche, ligne d'info ----

        function markLibrarySelection() {
            if (!libraryGrid) return;
            const id = state.libraryItem ? String(state.libraryItem.id) : null;
            libraryGrid.querySelectorAll('.drive-file').forEach(function (el) {
                const on = (id !== null && el.dataset.mediaId === id);
                el.classList.toggle('is-selected', on);
                el.setAttribute('aria-pressed', String(on));
            });
        }

        function filterLibraryGrid() {
            if (!libraryGrid || !librarySearch) return;
            const q = librarySearch.value.trim().toLowerCase();
            let shown = 0;
            libraryGrid.querySelectorAll('.drive-file').forEach(function (el) {
                const hit = !q || (el.dataset.search || '').indexOf(q) !== -1;
                el.hidden = !hit;
                if (hit) shown++;
            });
            // Un filtre qui ne renvoie rien doit le DIRE : une grille vide
            // ressemble sinon à un chargement qui n'a jamais abouti.
            let empty = libraryGrid.querySelector('.library-empty');
            if (q && shown === 0) {
                if (!empty) {
                    empty = document.createElement('p');
                    empty.className = 'hint library-empty';
                    libraryGrid.appendChild(empty);
                }
                empty.textContent = 'Aucun média ne correspond à « ' + librarySearch.value.trim() + ' ».';
                empty.hidden = false;
            } else if (empty) {
                empty.hidden = true;
            }
        }

        /** « Sélection : reddit · @memesfr · 1080×1350 · image ». */
        function updateSelectionInfo() {
            if (!wizSelInfo) return;
            if (!hasMedia() && !state.libraryItem) {
                wizSelInfo.textContent = 'Touche un média pour le choisir.';
                return;
            }
            const it = state.libraryItem;
            const bits = [];
            if (it) {
                if (it.platform) bits.push(it.platform);
                if (it.profile_username) bits.push('@' + it.profile_username);
                if (it.width && it.height) bits.push(it.width + '×' + it.height);
                bits.push(it.media_type === 'video' ? 'vidéo' : 'image');
            } else {
                if (state.imageName) bits.push(state.imageName);
                bits.push(state.mediaType === 'video' ? 'vidéo' : 'image');
            }
            wizSelInfo.textContent = 'Sélection : ' + bits.join(' · ');
        }

        // ---- Steppers ----
        // Tous suivent la même règle : lire le curseur, borner, écrire le
        // curseur, puis `dispatchEvent(new Event('input'))`. Le
        // gestionnaire d'origine fait le reste — aucune logique de rendu
        // n'est dupliquée ici.

        function nudgeRange(input, delta, min, max) {
            if (!input) return;
            const current = parseFloat(input.value);
            const next = Math.min(max, Math.max(min, current + delta));
            if (next === current) return;
            input.value = String(next);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }

        /** Interligne à la française : 1,2 — et 2,0 plutôt que 2. */
        function formatLineHeight(percent) {
            let t = (percent / 100).toFixed(2);
            if (t.charAt(t.length - 1) === '0') t = t.slice(0, -1);
            return t.replace('.', ',');
        }

        function syncStepperReadouts() {
            // Le zoom n'est plus un stepper partagé mais DEUX curseurs, un par
            // plateau : sa remise au diapason vit dans `syncZoomControls`.
            syncZoomControls();
            if (textSizeReadout && textSizeSlider) {
                textSizeReadout.textContent = Math.round(parseFloat(textSizeSlider.value)) + ' px';
            }
            if (lineHeightReadout && lineHeightSlider) {
                lineHeightReadout.textContent = formatLineHeight(parseFloat(lineHeightSlider.value));
            }
        }

        // ---- Plateau sélectionné ----
        // Sous 900px : le plateau AFFICHÉ (un seul tient à l'écran).
        // Au-dessus : les deux sont visibles, celui-ci porte le contour
        // d'accent. Une seule variable pour les deux tailles, donc aucun
        // écart possible entre le basculeur et le contour.

        function setSelectedPane(key) {
            const tt = (key === 'tt');
            if (stagesEl) stagesEl.classList.toggle('show-tt', tt);
            const switcher = document.getElementById('stage-switch');
            if (switcher) {
                switcher.querySelectorAll('.stage-switch__btn').forEach(function (b) {
                    const active = (b.dataset.stage === key);
                    b.classList.toggle('active', active);
                    b.setAttribute('aria-pressed', String(active));
                });
            }
            eachPane(function (p) {
                const stage = document.getElementById(p.stageId);
                if (stage) stage.classList.toggle('is-selected', p.key === key);
            });
            updateAllCanvasSizes();
        }

        // ---- Cartes d'export ----
        // Elles ne portent AUCUN état : le clic relaie sur l'interrupteur
        // du plateau (#toggle-ig / #toggle-tt), qui reste le seul point de
        // vérité de `p.enabled`. Impossible de désynchroniser une carte et
        // son plateau, puisque la carte ne fait que le relire.

        function syncExportCards() {
            if (!exportCards) return;
            eachPane(function (p) {
                const card = exportCards.querySelector('.export-card[data-pane="' + p.key + '"]');
                if (!card) return;
                card.classList.toggle('is-on', p.enabled);
                card.setAttribute('aria-pressed', String(p.enabled));
                const tag = document.getElementById('export-tag-' + p.key);
                if (tag) {
                    tag.textContent = p.enabled ? 'activé' : 'désactivé';
                    tag.classList.toggle('tag--accent', p.enabled);
                    tag.classList.toggle('tag--outline', !p.enabled);
                }
            });
            const dimsIG = document.getElementById('export-card-dims-ig');
            if (dimsIG) {
                const t = templateOf(panes.ig);
                const ratio = { 1080: '1:1', 1350: '4:5', 1920: '9:16' }[t.height] || '';
                dimsIG.textContent = t.width + '×' + t.height + (ratio ? ' · ' + ratio : '');
            }
        }

        /** Vignette réelle de chaque plateau, posée en fond des cartes. */
        function refreshExportShots() {
            if (!exportCards) return;
            eachPane(function (p) {
                const shot = document.getElementById('export-shot-' + p.key);
                if (!shot || !p.canvas) return;
                try {
                    // Même chemin que l'export : ce qu'on voit sur la carte
                    // EST ce qui sortira du fichier, repères en moins.
                    shot.style.backgroundImage =
                        'url("' + renderCanvasToDataURL(p, 'png', 1, 0.2) + '")';
                } catch (e) {
                    // Un aperçu manquant ne doit pas empêcher d'exporter.
                    console.warn('[editor] aperçu de plateau indisponible', e);
                }
            });
        }

        // ---- Navigation ----

        function wizSync() {
            const media = hasMedia();
            if (editorApp) editorApp.dataset.step = String(wizStep);
            if (wizStepTag) wizStepTag.textContent = 'Étape ' + wizStep + '/4';

            document.querySelectorAll('.wiz-rail__item').forEach(function (item) {
                const n = parseInt(item.dataset.goto, 10);
                item.classList.toggle('is-current', n === wizStep);
                item.classList.toggle('is-done', n < wizStep);
                item.disabled = (n > wizStep && !media);
                if (n === wizStep) item.setAttribute('aria-current', 'step');
                else item.removeAttribute('aria-current');
            });
            document.querySelectorAll('.wiz-steps__cell').forEach(function (cell) {
                const n = parseInt(cell.dataset.goto, 10);
                cell.classList.toggle('is-current', n === wizStep);
                cell.disabled = (n > wizStep && !media);
                const label = cell.querySelector('.wiz-steps__label');
                if (label) {
                    label.textContent = WIZ_LABELS[n - 1] + (n < wizStep ? ' ✓' : '');
                }
                if (n === wizStep) cell.setAttribute('aria-current', 'step');
                else cell.removeAttribute('aria-current');
            });

            if (wizNextBtn) {
                const nextLabel = WIZ_LABELS[wizStep] || '';
                wizNextBtn.textContent = 'Continuer — ' + nextLabel;
                // Sans média il n'y a rien à cadrer : le bouton le dit en
                // restant éteint plutôt qu'en ouvrant une étape vide.
                wizNextBtn.disabled = !media;
            }
            updateSelectionInfo();
            syncStepperReadouts();
            syncExportCards();
        }

        function goStep(n) {
            const target = Math.min(WIZ_LAST, Math.max(1, n));
            if (target > 1 && !hasMedia()) {
                note('Choisis d’abord un média : c’est lui que les trois étapes suivantes cadrent, habillent et exportent.', 'error');
                return;
            }
            wizStep = target;

            // Étape 3 : la phrase écrite au Tri rapide remplit le bandeau,
            // UNE SEULE FOIS et seulement si le bandeau est vide — on
            // n'écrase jamais ce que l'utilisateur a tapé.
            let prefilled = false;
            if (wizStep === 3 && state.phrase && !state.phraseUsed && !state.text) {
                state.phraseUsed = true;
                memeTextInput.value = state.phrase;
                updateText(state.phrase);
                prefilled = true;
            }
            if (wizPrefillNote) {
                wizPrefillNote.style.display = (wizStep === 3 && prefilled) ? 'block' : 'none';
            }

            wizSync();
            // La grille des tiers est un repère de CADRAGE : elle
            // n'existe qu'à l'étape 2.
            showThirdsGrid(wizStep === 2);
            // Le plateau change de taille d'une étape à l'autre (il
            // disparaît aux étapes 1 et 4 sous 900px) : on le recalcule
            // APRÈS que la CSS a repris la main.
            updateAllCanvasSizes();
            if (wizStep === 4) refreshExportShots();
            const scroller = document.querySelector('.wiz-panel__scroll');
            if (scroller) scroller.scrollTop = 0;
        }

        function setupWizard() {
            // ---- Barre d'étapes, rail, Retour / Continuer ----
            document.querySelectorAll('[data-goto]').forEach(function (el) {
                el.addEventListener('click', function () {
                    goStep(parseInt(el.dataset.goto, 10));
                });
            });
            if (wizNextBtn) wizNextBtn.addEventListener('click', function () { goStep(wizStep + 1); });
            if (wizPrevBtn) wizPrevBtn.addEventListener('click', function () { goStep(wizStep - 1); });
            if (wizBackBtn) {
                wizBackBtn.addEventListener('click', function () {
                    // Depuis l'étape 1, « ← » quitte l'éditeur pour la
                    // galerie : c'est de là qu'on vient.
                    if (wizStep > 1) goStep(wizStep - 1);
                    else window.location.href = '/viewer';
                });
            }

            // ---- Basculeur / sélection de plateau ----
            const switcher = document.getElementById('stage-switch');
            if (switcher) {
                switcher.addEventListener('click', function (e) {
                    const btn = e.target.closest('.stage-switch__btn');
                    if (btn) setSelectedPane(btn.dataset.stage);
                });
            }
            // Sur desktop les deux plateaux sont là : cliquer sur la
            // légende de l'un le désigne. Le canvas lui-même reste à
            // Fabric — on n'intercepte QUE l'en-tête.
            eachPane(function (p) {
                const stage = document.getElementById(p.stageId);
                const head = stage ? stage.querySelector('.stage-head') : null;
                if (!head) return;
                head.addEventListener('click', function (e) {
                    // L'interrupteur de fabrication garde son rôle.
                    if (e.target.closest('.stage-toggle')) return;
                    setSelectedPane(p.key);
                });
            });

            // ---- Zoom du média : stepper 100 → 200 %, pas de 10 ----
            // Le curseur « Zoom fin » (50 → 200) reste la source de
            // vérité et reste atteignable dans la retouche avancée :
            // aucune valeur possible avant ne devient impossible.
            // UN CURSEUR PAR PLATEAU — chacun ne touche QUE le sien.
            [['ig', panes.ig], ['tt', panes.tt]].forEach(function(paire) {
                const suffixe = paire[0], pane = paire[1];
                const curseur = document.getElementById('zoom-' + suffixe);
                const lecture = document.getElementById('zoom-readout-' + suffixe);
                if (!curseur) return;
                curseur.addEventListener('input', function () {
                    const v = parseInt(curseur.value, 10);
                    if (lecture) lecture.textContent = v + ' %';
                    appliquerZoom(pane, v);
                });
            });

            // ---- Taille du texte : 24 → 72 px, pas de 2 ----
            // Le stepper a laissé place au CURSEUR #text-size lui-même, remonté
            // dans le panneau. Il porte déjà son propre écouteur `input` : il
            // n'y a plus rien à câbler ici, et les deux boutons ont disparu du
            // gabarit — garder leurs gestionnaires aurait été du code mort.

            // ---- Interligne : 0,8 → 2,0, pas de 0,1 ----
            const lineUp = document.getElementById('line-height-up');
            const lineDown = document.getElementById('line-height-down');
            if (lineUp) lineUp.addEventListener('click', function () { nudgeRange(lineHeightSlider, 10, 80, 200); });
            if (lineDown) lineDown.addEventListener('click', function () { nudgeRange(lineHeightSlider, -10, 80, 200); });

            // Les curseurs restent maîtres : tout mouvement, d'où qu'il
            // vienne, redescend dans les afficheurs.
            [imageScaleSlider, textSizeSlider, lineHeightSlider].forEach(function (input) {
                if (input) input.addEventListener('input', syncStepperReadouts);
            });

            // ---- « Tout réinitialiser » de l'étape Cadrage ----
            // Zoom, position, hauteur de cadre, rotation, miroirs — la
            // liste exacte du handoff, et RIEN d'autre : le texte et le
            // format de sortie ne sont pas du cadrage.
            const cropResetAll = document.getElementById('crop-reset-all');
            if (cropResetAll) {
                cropResetAll.addEventListener('click', function () {
                    eachPane(function (p) {
                        p.imageOffsetX = 0;
                        p.imageOffsetY = 0;
                        p.imageScale = 100;
                    });
                    if (imageScaleSlider) {
                        imageScaleSlider.value = 100;
                        updateImageScale(100);
                    }
                    if (frameHeightSlider) {
                        frameHeightSlider.value = 100;
                        updateFrameHeight(100);
                    }
                    state.rotation = 0;
                    state.flipX = false;
                    state.flipY = false;
                    syncImageEditControls();
                    if (panes.ig.imageObj || panes.tt.imageObj) reapplyImageTransforms();
                    syncStepperReadouts();
                    note('Cadrage réinitialisé : zoom, position, hauteur, rotation et miroirs.', 'success');
                });
            }

            // ---- Recherche dans la bibliothèque ----
            if (librarySearch) {
                librarySearch.addEventListener('input', filterLibraryGrid);
            }

            // ---- Cartes d'export ----
            if (exportCards) {
                exportCards.addEventListener('click', function (e) {
                    const card = e.target.closest('.export-card');
                    if (!card) return;
                    const toggle = document.getElementById('toggle-' + card.dataset.pane);
                    if (toggle) toggle.click();   // un seul point de vérité
                    syncExportCards();
                });
            }
            // L'interrupteur peut aussi être actionné depuis la légende du
            // plateau : les cartes se relisent après coup.
            eachPane(function (p) {
                const toggle = document.getElementById('toggle-' + p.key);
                if (toggle) toggle.addEventListener('click', function () {
                    // Après le gestionnaire d'origine, qui a déjà basculé
                    // `p.enabled` (même phase, ordre d'inscription).
                    syncExportCards();
                });
            });

            // ---- La recherche ne concerne que la bibliothèque ----
            importTabs.forEach(function (tab) {
                tab.addEventListener('click', function () {
                    if (librarySearchWrap) {
                        librarySearchWrap.style.display =
                            tab.dataset.source === 'library' ? 'block' : 'none';
                    }
                });
            });

            // ---- Franchissement du seuil 900px ----
            const onViewChange = function () { updateAllCanvasSizes(); };
            if (mobileViewMq.addEventListener) mobileViewMq.addEventListener('change', onViewChange);
            else if (mobileViewMq.addListener) mobileViewMq.addListener(onViewChange);

            // ---- État de départ ----
            setSelectedPane('ig');
            // L'onglet actif du balisage est « Bibliothèque » : on charge
            // sa grille sans attendre un clic qui n'aura pas lieu.
            loadLibraryMedia();
            goStep(1);
        }

        // ============================================
        // INIT
        // ============================================
        function init() {
            loadLogo(); // Preload logo for watermark
            initCanvases();
            setupEventListeners();
            setupWizard();
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
