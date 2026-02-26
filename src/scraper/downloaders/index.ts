import { downloadMedia, type DownloadResult } from './media.js';
import { downloadHls, isHlsUrl } from './hls.js';
import { createChildLogger } from '../../utils/logger.js';

const log = createChildLogger('download-router');

export type { DownloadResult };

export async function download(url: string, filename?: string): Promise<DownloadResult> {
  if (isHlsUrl(url)) {
    log.info('Using HLS downloader');
    return downloadHls(url, filename);
  }

  log.info('Using direct downloader');
  return downloadMedia(url, filename);
}
