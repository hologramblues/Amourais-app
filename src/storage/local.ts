import { readdirSync, statSync, unlinkSync } from 'node:fs';
import { resolve } from 'node:path';
import { config } from '../config/index.js';
import { createChildLogger } from '../utils/logger.js';

const log = createChildLogger('local-storage');

export function cleanupOldFiles(maxAgeMs: number = 24 * 60 * 60 * 1000): number {
  const dir = config.paths.downloadDir;
  let cleaned = 0;

  try {
    const files = readdirSync(dir);
    const now = Date.now();

    for (const file of files) {
      const filePath = resolve(dir, file);
      try {
        const stat = statSync(filePath);
        if (now - stat.mtimeMs > maxAgeMs) {
          unlinkSync(filePath);
          cleaned++;
        }
      } catch {}
    }

    if (cleaned > 0) {
      log.info({ cleaned }, 'Cleaned up old download files');
    }
  } catch (err) {
    log.error({ err }, 'Failed to clean up download directory');
  }

  return cleaned;
}

export function getDownloadDirSize(): { files: number; bytes: number } {
  const dir = config.paths.downloadDir;
  let files = 0;
  let bytes = 0;

  try {
    const entries = readdirSync(dir);
    for (const entry of entries) {
      try {
        const stat = statSync(resolve(dir, entry));
        if (stat.isFile()) {
          files++;
          bytes += stat.size;
        }
      } catch {}
    }
  } catch {}

  return { files, bytes };
}
