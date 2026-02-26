import ffmpeg from 'fluent-ffmpeg';
import { resolve } from 'node:path';
import { config } from '../../config/index.js';
import { createChildLogger } from '../../utils/logger.js';
import { nanoid } from 'nanoid';
import { statSync } from 'node:fs';
import type { DownloadResult } from './media.js';

const log = createChildLogger('hls-downloader');

export function isHlsUrl(url: string): boolean {
  return url.includes('.m3u8') || url.includes('manifest');
}

export async function downloadHls(url: string, filename?: string): Promise<DownloadResult> {
  const finalFilename = filename || `${nanoid(10)}.mp4`;
  const localPath = resolve(config.paths.downloadDir, finalFilename);

  log.info({ url: url.substring(0, 80), localPath }, 'Downloading HLS stream');

  return new Promise((resolve, reject) => {
    ffmpeg(url)
      .inputOptions(['-protocol_whitelist', 'file,http,https,tcp,tls,crypto'])
      .outputOptions(['-c', 'copy', '-bsf:a', 'aac_adtstoasc'])
      .output(localPath)
      .on('start', (cmd) => log.debug({ cmd }, 'ffmpeg started'))
      .on('progress', (progress) => {
        if (progress.percent) {
          log.debug({ percent: Math.round(progress.percent) }, 'ffmpeg progress');
        }
      })
      .on('end', () => {
        const stats = statSync(localPath);
        log.info({ localPath, fileSize: stats.size }, 'HLS download complete');
        resolve({
          localPath,
          fileSize: stats.size,
          mimeType: 'video/mp4',
        });
      })
      .on('error', (err) => {
        log.error({ err }, 'ffmpeg failed');
        reject(err);
      })
      .run();
  });
}
