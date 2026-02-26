import { createWriteStream } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import { request } from 'undici';
import { resolve, extname } from 'node:path';
import { config } from '../../config/index.js';
import { createChildLogger } from '../../utils/logger.js';
import { nanoid } from 'nanoid';

const log = createChildLogger('downloader');

export interface DownloadResult {
  localPath: string;
  fileSize: number;
  mimeType: string;
}

export async function downloadMedia(url: string, filename?: string): Promise<DownloadResult> {
  const ext = guessExtension(url);
  const finalFilename = filename || `${nanoid(10)}${ext}`;
  const localPath = resolve(config.paths.downloadDir, finalFilename);

  log.info({ url: url.substring(0, 80), localPath }, 'Downloading media');

  const { statusCode, headers, body } = await request(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      'Accept': '*/*',
      'Referer': guessReferer(url),
    },
    maxRedirections: 5,
  });

  if (statusCode !== 200) {
    const text = await body.text();
    throw new Error(`Download failed: HTTP ${statusCode} - ${text.substring(0, 200)}`);
  }

  const writeStream = createWriteStream(localPath);
  await pipeline(body, writeStream);

  const fileSize = writeStream.bytesWritten;
  const mimeType = (headers['content-type'] as string) || 'application/octet-stream';

  log.info({ localPath, fileSize, mimeType }, 'Download complete');

  return { localPath, fileSize, mimeType };
}

function guessExtension(url: string): string {
  const urlPath = new URL(url).pathname;
  const ext = extname(urlPath).split('?')[0];

  if (ext && ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4', '.mov', '.webm'].includes(ext.toLowerCase())) {
    return ext.toLowerCase();
  }

  // Guess from URL patterns
  if (url.includes('.m3u8')) return '.mp4';
  if (url.includes('video')) return '.mp4';
  return '.jpg';
}

function guessReferer(url: string): string {
  if (url.includes('instagram') || url.includes('cdninstagram') || url.includes('fbcdn')) {
    return 'https://www.instagram.com/';
  }
  if (url.includes('tiktok')) {
    return 'https://www.tiktok.com/';
  }
  if (url.includes('twimg') || url.includes('x.com') || url.includes('twitter')) {
    return 'https://x.com/';
  }
  return '';
}
