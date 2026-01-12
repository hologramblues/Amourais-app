const express = require('express');
const cors = require('cors');
const multer = require('multer');
const ffmpeg = require('fluent-ffmpeg');
const path = require('path');
const fs = require('fs');
const { v4: uuidv4 } = require('uuid');

const app = express();
const PORT = process.env.PORT || 3000;

// Ensure temp directories exist
const UPLOAD_DIR = '/tmp/uploads';
const OUTPUT_DIR = '/tmp/outputs';
[UPLOAD_DIR, OUTPUT_DIR].forEach(dir => {
    if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
    }
});

// Middleware
app.use(cors());
app.use(express.json());

// Multer config for video uploads
const storage = multer.diskStorage({
    destination: UPLOAD_DIR,
    filename: (req, file, cb) => {
        const uniqueName = `${uuidv4()}${path.extname(file.originalname)}`;
        cb(null, uniqueName);
    }
});

const upload = multer({
    storage,
    limits: { fileSize: 100 * 1024 * 1024 }, // 100MB max
    fileFilter: (req, file, cb) => {
        if (file.mimetype.startsWith('video/')) {
            cb(null, true);
        } else {
            cb(new Error('Only video files are allowed'));
        }
    }
});

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'ok', ffmpeg: true });
});

// Main video processing endpoint
app.post('/api/process-video', upload.single('video'), async (req, res) => {
    if (!req.file) {
        return res.status(400).json({ error: 'No video file uploaded' });
    }

    const inputPath = req.file.path;
    const outputId = uuidv4();
    const outputPath = path.join(OUTPUT_DIR, `${outputId}.mp4`);

    try {
        // Parse parameters from request
        const params = JSON.parse(req.body.params || '{}');
        
        const {
            // Template dimensions
            templateWidth = 1080,
            templateHeight = 1080,
            // Frame position and size
            frameX = 54,
            frameY = 195,
            frameWidth = 972,
            frameHeight = 810,
            frameRadius = 27,
            // Trim times
            trimStart = 0,
            trimEnd = 10,
            // Media position/scale (relative to frame center)
            imageScale = 100,
            imageOffsetX = 0,
            imageOffsetY = 0,
            // Text
            text = '',
            textSize = 42,
            textX = 54,
            textY = 40,
            // Overlay text
            overlayText = '',
            // Watermark
            watermarkX = 1010,
            watermarkY = 1040,
            // Watermark opacity (0-100)
            watermarkOpacity = 100,
        } = params;

        console.log('Processing video with params:', params);

        // Build FFmpeg filter complex
        const filters = buildFilterComplex({
            templateWidth,
            templateHeight,
            frameX,
            frameY,
            frameWidth,
            frameHeight,
            frameRadius,
            imageScale,
            imageOffsetX,
            imageOffsetY,
            text,
            textSize,
            textX,
            textY,
            overlayText,
            watermarkX,
            watermarkY,
            watermarkOpacity
        });

        console.log('FFmpeg filters:', filters);

        // Process video with FFmpeg
        await processVideo(inputPath, outputPath, {
            trimStart,
            trimEnd,
            filters,
            templateWidth,
            templateHeight
        });

        // Send file back
        res.download(outputPath, `samourais_meme_${outputId}.mp4`, (err) => {
            // Cleanup files after download
            cleanupFiles([inputPath, outputPath]);
            if (err) {
                console.error('Download error:', err);
            }
        });

    } catch (error) {
        console.error('Processing error:', error);
        cleanupFiles([inputPath, outputPath]);
        res.status(500).json({ error: error.message });
    }
});

// Build FFmpeg filter complex string
function buildFilterComplex(params) {
    const {
        templateWidth,
        templateHeight,
        frameX,
        frameY,
        frameWidth,
        frameHeight,
        imageScale,
        imageOffsetX,
        imageOffsetY,
        text,
        textSize,
        textX,
        textY,
        overlayText,
        watermarkX,
        watermarkY,
        watermarkOpacity
    } = params;

    // Calculate video scaling and positioning
    const scaleFactor = imageScale / 100;
    
    // Frame center for positioning
    const frameCenterX = frameX + frameWidth / 2;
    const frameCenterY = frameY + frameHeight / 2;

    const filters = [];
    let currentLabel = '0:v'; // Start with input video

    // Step 1: Scale input video to cover frame area
    const scaleFilter = 
        `[${currentLabel}]scale=w='max(${frameWidth}*${scaleFactor}\\,ih*${frameWidth}/${frameHeight}*${scaleFactor})':` +
        `h='max(${frameHeight}*${scaleFactor}\\,iw*${frameHeight}/${frameWidth}*${scaleFactor})':` +
        `force_original_aspect_ratio=increase[scaled]`;
    filters.push(scaleFilter);
    currentLabel = 'scaled';

    // Step 2: Create white background and overlay scaled video
    const videoX = Math.round(frameCenterX + imageOffsetX);
    const videoY = Math.round(frameCenterY + imageOffsetY);
    
    filters.push(
        `color=white:s=${templateWidth}x${templateHeight}:r=30[bg]`
    );
    filters.push(
        `[bg][${currentLabel}]overlay=x='${videoX}-overlay_w/2':y='${videoY}-overlay_h/2':shortest=1[with_video]`
    );
    currentLabel = 'with_video';

    // Step 3: Crop to frame area and re-overlay on clean background
    filters.push(
        `[${currentLabel}]crop=${frameWidth}:${frameHeight}:${frameX}:${frameY}[cropped]`
    );
    filters.push(
        `color=white:s=${templateWidth}x${templateHeight}:r=30[bg2]`
    );
    filters.push(
        `[bg2][cropped]overlay=${frameX}:${frameY}:shortest=1[composed]`
    );
    currentLabel = 'composed';

    // Step 4: Add top text (meme text)
    if (text && text.trim()) {
        const escapedText = escapeFFmpegText(text);
        // Wrap text if too long
        const maxCharsPerLine = Math.floor(templateWidth / (textSize * 0.6));
        const wrappedText = wrapText(escapedText, maxCharsPerLine);
        
        filters.push(
            `[${currentLabel}]drawtext=text='${wrappedText}':` +
            `fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:` +
            `fontsize=${textSize}:fontcolor=black:` +
            `x=(w-text_w)/2:y=${textY}[with_text]`
        );
        currentLabel = 'with_text';
    }

    // Step 5: Add overlay text (Impact style on video area)
    if (overlayText && overlayText.trim()) {
        const escapedOverlay = escapeFFmpegText(overlayText.toUpperCase());
        const overlayY = frameY + frameHeight - 80;
        
        filters.push(
            `[${currentLabel}]drawtext=text='${escapedOverlay}':` +
            `fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:` +
            `fontsize=${Math.round(templateWidth * 0.055)}:fontcolor=white:` +
            `borderw=4:bordercolor=black:` +
            `x=(w-text_w)/2:y=${overlayY}[with_overlay]`
        );
        currentLabel = 'with_overlay';
    }

    // Step 6: Add watermark text
    const escapedWatermark = escapeFFmpegText('SAMOURAIS');
    const wmOpacity = Math.max(0, Math.min(1, watermarkOpacity / 100));
    
    // Convert opacity to hex alpha (0-255)
    const alphaHex = Math.round(wmOpacity * 255).toString(16).padStart(2, '0');
    
    filters.push(
        `[${currentLabel}]drawtext=text='${escapedWatermark}':` +
        `fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:` +
        `fontsize=${Math.round(templateWidth * 0.04)}:fontcolor=white@${wmOpacity}:` +
        `borderw=2:bordercolor=0x333333@${wmOpacity}:` +
        `x=${watermarkX}-text_w:y=${watermarkY}-text_h[final]`
    );

    return filters.join(';');
}

// Wrap text for multi-line display
function wrapText(text, maxChars) {
    if (text.length <= maxChars) return text;
    
    const words = text.split(' ');
    const lines = [];
    let currentLine = '';
    
    for (const word of words) {
        if ((currentLine + ' ' + word).trim().length <= maxChars) {
            currentLine = (currentLine + ' ' + word).trim();
        } else {
            if (currentLine) lines.push(currentLine);
            currentLine = word;
        }
    }
    if (currentLine) lines.push(currentLine);
    
    // FFmpeg uses \n for newlines in drawtext, but we need to escape it
    return lines.join('\\n');
}

// Escape text for FFmpeg drawtext filter
function escapeFFmpegText(text) {
    return text
        .replace(/\\/g, '\\\\\\\\')  // Escape backslashes
        .replace(/'/g, "'\\''")       // Escape single quotes
        .replace(/:/g, '\\:')         // Escape colons
        .replace(/\[/g, '\\[')        // Escape brackets
        .replace(/\]/g, '\\]')
        .replace(/%/g, '\\%');        // Escape percent signs
}

// Process video with FFmpeg
function processVideo(inputPath, outputPath, options) {
    const { trimStart, trimEnd, filters, templateWidth, templateHeight } = options;
    const duration = trimEnd - trimStart;

    return new Promise((resolve, reject) => {
        let command = ffmpeg(inputPath)
            .setStartTime(trimStart)
            .setDuration(duration)
            .complexFilter(filters, 'final')
            .outputOptions([
                '-map', '[final]',
                '-map', '0:a?', // Include audio if present
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ar', '44100', // Audio sample rate
                '-movflags', '+faststart',
                '-pix_fmt', 'yuv420p',
                '-r', '30', // Frame rate
                '-shortest' // End when shortest stream ends
            ])
            .on('start', (cmd) => {
                console.log('FFmpeg command:', cmd);
            })
            .on('progress', (progress) => {
                if (progress.percent) {
                    console.log('Processing:', progress.percent.toFixed(1) + '%');
                }
            })
            .on('end', () => {
                console.log('Processing complete');
                resolve();
            })
            .on('error', (err, stdout, stderr) => {
                console.error('FFmpeg error:', err.message);
                console.error('FFmpeg stderr:', stderr);
                reject(err);
            })
            .save(outputPath);
    });
}

// Cleanup temporary files
function cleanupFiles(files) {
    files.forEach(file => {
        try {
            if (file && fs.existsSync(file)) {
                fs.unlinkSync(file);
            }
        } catch (e) {
            console.error('Cleanup error:', e);
        }
    });
}

// Error handling middleware
app.use((error, req, res, next) => {
    console.error('Server error:', error);
    res.status(500).json({ error: error.message });
});

// Start server
app.listen(PORT, () => {
    console.log(`🎬 Samourais Meme Backend running on port ${PORT}`);
    console.log(`Health check: http://localhost:${PORT}/health`);
});
