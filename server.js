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
            watermarkY
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
        watermarkY
    } = params;

    // Calculate video scaling and positioning
    const scaleFactor = imageScale / 100;
    
    // The video needs to fill the frame area
    // We'll scale it to cover the frame, then position it
    const frameCenterX = frameX + frameWidth / 2;
    const frameCenterY = frameY + frameHeight / 2;

    const filters = [];

    // Step 1: Create white background
    filters.push(`color=white:s=${templateWidth}x${templateHeight}:d=1[bg]`);

    // Step 2: Scale input video to cover frame area
    // We need to calculate the scale to ensure the video covers the frame
    filters.push(
        `[0:v]scale=w='if(gt(a,${frameWidth}/${frameHeight}),${frameHeight}*${scaleFactor}*a,${frameWidth}*${scaleFactor})':` +
        `h='if(gt(a,${frameWidth}/${frameHeight}),${frameHeight}*${scaleFactor},${frameWidth}*${scaleFactor}/a)'[scaled]`
    );

    // Step 3: Create rounded rectangle mask for the frame
    // Using drawbox with rounded corners approximation
    filters.push(
        `color=black@0:s=${templateWidth}x${templateHeight}[mask_bg]`
    );
    filters.push(
        `[mask_bg]drawbox=x=${frameX}:y=${frameY}:w=${frameWidth}:h=${frameHeight}:c=white:t=fill[mask]`
    );

    // Step 4: Position the scaled video
    const videoX = Math.round(frameCenterX + imageOffsetX);
    const videoY = Math.round(frameCenterY + imageOffsetY);
    
    filters.push(
        `[bg][scaled]overlay=x='${videoX}-overlay_w/2':y='${videoY}-overlay_h/2'[with_video]`
    );

    // Step 5: Apply mask (crop to frame) - simplified approach using crop and overlay
    filters.push(
        `[with_video]crop=${frameWidth}:${frameHeight}:${frameX}:${frameY}[cropped]`
    );
    filters.push(
        `color=white:s=${templateWidth}x${templateHeight}[bg2]`
    );
    filters.push(
        `[bg2][cropped]overlay=${frameX}:${frameY}[with_frame]`
    );

    // Step 6: Add top text
    if (text) {
        const escapedText = escapeFFmpegText(text);
        filters.push(
            `[with_frame]drawtext=text='${escapedText}':` +
            `fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:` +
            `fontsize=${textSize}:fontcolor=black:` +
            `x=${textX}:y=${textY}[with_text]`
        );
    } else {
        filters.push(`[with_frame]null[with_text]`);
    }

    // Step 7: Add overlay text (Impact style on video)
    if (overlayText) {
        const escapedOverlay = escapeFFmpegText(overlayText.toUpperCase());
        const overlayY = frameY + frameHeight - 60;
        filters.push(
            `[with_text]drawtext=text='${escapedOverlay}':` +
            `fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:` +
            `fontsize=${templateWidth * 0.055}:fontcolor=white:` +
            `borderw=4:bordercolor=black:` +
            `x=(w-text_w)/2:y=${overlayY}[with_overlay]`
        );
    } else {
        filters.push(`[with_text]null[with_overlay]`);
    }

    // Step 8: Add watermark text
    const escapedWatermark = escapeFFmpegText('SAMOURAIS');
    filters.push(
        `[with_overlay]drawtext=text='${escapedWatermark}':` +
        `fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:` +
        `fontsize=${templateWidth * 0.04}:fontcolor=white:` +
        `borderw=2:bordercolor=0x333333:` +
        `x=${watermarkX}-text_w:y=${watermarkY}-text_h[final]`
    );

    return filters.join(';');
}

// Escape text for FFmpeg drawtext filter
function escapeFFmpegText(text) {
    return text
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "'\\''")
        .replace(/:/g, '\\:')
        .replace(/\[/g, '\\[')
        .replace(/\]/g, '\\]');
}

// Process video with FFmpeg
function processVideo(inputPath, outputPath, options) {
    const { trimStart, trimEnd, filters, templateWidth, templateHeight } = options;
    const duration = trimEnd - trimStart;

    return new Promise((resolve, reject) => {
        ffmpeg(inputPath)
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
                '-movflags', '+faststart',
                '-pix_fmt', 'yuv420p',
                '-s', `${templateWidth}x${templateHeight}`
            ])
            .on('start', (cmd) => {
                console.log('FFmpeg command:', cmd);
            })
            .on('progress', (progress) => {
                console.log('Processing:', progress.percent?.toFixed(1) + '%');
            })
            .on('end', () => {
                console.log('Processing complete');
                resolve();
            })
            .on('error', (err) => {
                console.error('FFmpeg error:', err);
                reject(err);
            })
            .save(outputPath);
    });
}

// Cleanup temporary files
function cleanupFiles(files) {
    files.forEach(file => {
        try {
            if (fs.existsSync(file)) {
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
