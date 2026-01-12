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

// Multer config for video + template uploads
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
});

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'ok', ffmpeg: true });
});

// Main video processing endpoint
app.post('/api/process-video', upload.fields([
    { name: 'video', maxCount: 1 },
    { name: 'template', maxCount: 1 }
]), async (req, res) => {
    if (!req.files || !req.files.video) {
        return res.status(400).json({ error: 'No video file uploaded' });
    }
    if (!req.files.template) {
        return res.status(400).json({ error: 'No template file uploaded' });
    }

    const videoPath = req.files.video[0].path;
    const templatePath = req.files.template[0].path;
    const outputId = uuidv4();
    const outputPath = path.join(OUTPUT_DIR, `${outputId}.mp4`);

    try {
        // Parse parameters from request
        const params = JSON.parse(req.body.params || '{}');
        
        const {
            // Template dimensions
            templateWidth = 1080,
            templateHeight = 1080,
            // Frame position and size (where video goes)
            frameX = 54,
            frameY = 195,
            frameWidth = 972,
            frameHeight = 810,
            // Trim times
            trimStart = 0,
            trimEnd = 10,
            // Video position/scale (relative to frame center)
            imageScale = 100,
            imageOffsetX = 0,
            imageOffsetY = 0,
        } = params;

        console.log('Processing video with params:', params);

        // Process video with FFmpeg
        await processVideo(videoPath, templatePath, outputPath, {
            templateWidth,
            templateHeight,
            frameX,
            frameY,
            frameWidth,
            frameHeight,
            trimStart,
            trimEnd,
            imageScale,
            imageOffsetX,
            imageOffsetY
        });

        // Send file back
        res.download(outputPath, `samourais_meme_${outputId}.mp4`, (err) => {
            // Cleanup files after download
            cleanupFiles([videoPath, templatePath, outputPath]);
            if (err) {
                console.error('Download error:', err);
            }
        });

    } catch (error) {
        console.error('Processing error:', error);
        cleanupFiles([videoPath, templatePath, outputPath]);
        res.status(500).json({ error: error.message });
    }
});

// Process video with FFmpeg - overlay template on top of video
function processVideo(videoPath, templatePath, outputPath, options) {
    const {
        templateWidth,
        templateHeight,
        frameX,
        frameY,
        frameWidth,
        frameHeight,
        trimStart,
        trimEnd,
        imageScale,
        imageOffsetX,
        imageOffsetY
    } = options;

    const duration = trimEnd - trimStart;
    const scaleFactor = imageScale / 100;

    // Calculate video positioning
    const frameCenterX = frameX + frameWidth / 2;
    const frameCenterY = frameY + frameHeight / 2;
    const videoX = Math.round(frameCenterX + imageOffsetX);
    const videoY = Math.round(frameCenterY + imageOffsetY);

    // Calculate scaled video size to cover frame
    const scaledWidth = Math.round(frameWidth * scaleFactor * 1.2);

    // FFmpeg filter:
    // 1. Create white background
    // 2. Scale video to cover frame area
    // 3. Overlay video centered on frame position
    // 4. Overlay template PNG on top (with alpha transparency)
    const filterComplex = [
        // Create white background at template size
        `color=white:s=${templateWidth}x${templateHeight}:r=30[bg]`,
        // Scale input video
        `[0:v]scale=${scaledWidth}:-1[scaled]`,
        // Overlay video on background, centered at frame position
        `[bg][scaled]overlay=x=${videoX}-overlay_w/2:y=${videoY}-overlay_h/2:shortest=1[with_video]`,
        // Overlay template PNG on top (template has transparent hole for video)
        `[with_video][1:v]overlay=0:0:shortest=1[final]`
    ].join(';');

    console.log('FFmpeg filter:', filterComplex);

    return new Promise((resolve, reject) => {
        ffmpeg()
            .input(videoPath)
            .setStartTime(trimStart)
            .setDuration(duration)
            .input(templatePath)
            .complexFilter(filterComplex, 'final')
            .outputOptions([
                '-map', '0:a?',
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ar', '44100',
                '-movflags', '+faststart',
                '-pix_fmt', 'yuv420p',
                '-r', '30',
                '-shortest'
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
