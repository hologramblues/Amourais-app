import type Hero from '@ulixee/hero';
import type { PlatformExtractor, ExtractorResult, MediaItem, ProfileInfo, ExtractOptions } from './base.js';
import { scrollAndCollect } from '../scroller.js';
import { createChildLogger } from '../../utils/logger.js';
import { sleep } from '../../utils/retry.js';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

const log = createChildLogger('tiktok');

export class TikTokExtractor implements PlatformExtractor {
  platform = 'tiktok' as const;

  async extract(hero: Hero, profileUrl: string, knownPostIds: Set<string>, options?: ExtractOptions): Promise<ExtractorResult> {
    const mode = options?.scrapeMode || 'daily';
    const media: MediaItem[] = [];
    const apiResponses: any[] = [];
    let profileInfo: ProfileInfo = {};
    let hitKnownContent = false;
    const twoYearsAgo = new Date();
    twoYearsAgo.setFullYear(twoYearsAgo.getFullYear() - 2);
    let reachedTimeLimit = false;

    // Load session cookies
    await this.loadCookies(hero);

    // Intercept API responses containing video data
    hero.activeTab.on('resource', async (event: any) => {
      try {
        const url = typeof event.url === 'string' ? event.url : String(event.url);
        if (url.includes('/api/post/item_list') || url.includes('/api/user/detail')) {
          const response = await event.response;
          if (response?.headers?.['content-type']?.includes('json')) {
            const text = await response.text();
            try {
              apiResponses.push(JSON.parse(text));
            } catch {}
          }
        }
      } catch {}
    });

    log.info({ profileUrl }, 'Navigating to TikTok profile');
    await hero.goto(profileUrl);
    await hero.waitForPaintingStable();
    await sleep(3000);

    // Extract from embedded hydration data
    const hydrationMedia = await this.extractFromHydration(hero, knownPostIds);
    for (const item of hydrationMedia) {
      if (knownPostIds.has(item.postId)) {
        hitKnownContent = true;
      } else {
        media.push(item);
      }
    }

    // Extract profile info
    profileInfo = await this.extractProfileInfo(hero);

    // Scroll for more content
    const shouldScroll = mode === 'backfill' ? !reachedTimeLimit : !hitKnownContent;
    if (shouldScroll) {
      await scrollAndCollect(hero, {
        maxScrolls: options?.maxScrolls || 20,
        scrollPauseMs: 3500,
        shouldStop: () => mode === 'backfill' ? reachedTimeLimit : hitKnownContent,
        onScroll: () => {
          while (apiResponses.length > 0) {
            const response = apiResponses.shift();
            const items = this.parseApiResponse(response);
            for (const item of items) {
              if (mode === 'backfill' && item.postedAt && item.postedAt < twoYearsAgo) {
                reachedTimeLimit = true;
              }
              if (knownPostIds.has(item.postId)) {
                if (mode === 'daily') hitKnownContent = true;
              } else if (!media.some(m => m.postId === item.postId)) {
                media.push(item);
              }
            }
          }
        },
      });
    }

    // Process remaining API responses
    while (apiResponses.length > 0) {
      const response = apiResponses.shift();
      const items = this.parseApiResponse(response);
      for (const item of items) {
        if (!knownPostIds.has(item.postId) && !media.some(m => m.postId === item.postId)) {
          media.push(item);
        }
      }
    }

    log.info({ mediaCount: media.length }, 'TikTok extraction complete');
    return { profileInfo, media };
  }

  private async loadCookies(hero: Hero): Promise<void> {
    const cookiePath = resolve('data/sessions/tiktok.json');
    if (!existsSync(cookiePath)) return;

    try {
      const cookiesRaw = readFileSync(cookiePath, 'utf-8');
      const cookies = JSON.parse(cookiesRaw);
      if (Array.isArray(cookies)) {
        for (const cookie of cookies) {
          try {
            await hero.activeTab.cookieStorage.setItem(cookie.name, cookie.value, {
              domain: cookie.domain || '.tiktok.com',
              path: cookie.path || '/',
              secure: cookie.secure ?? true,
              httpOnly: cookie.httpOnly ?? false,
            });
          } catch {}
        }
        log.info({ count: cookies.length }, 'TikTok cookies loaded');
      }
    } catch (err) {
      log.error({ err }, 'Failed to load TikTok cookies');
    }
  }

  private async extractProfileInfo(hero: Hero): Promise<ProfileInfo> {
    try {
      const ogTitle = await hero.document.querySelector('meta[property="og:title"]');
      const displayName = ogTitle ? await (ogTitle as any).getAttribute('content') : undefined;

      const ogImage = await hero.document.querySelector('meta[property="og:image"]');
      const avatarUrl = ogImage ? await (ogImage as any).getAttribute('content') : undefined;

      return { displayName, avatarUrl };
    } catch {
      return {};
    }
  }

  private async extractFromHydration(hero: Hero, knownPostIds: Set<string>): Promise<MediaItem[]> {
    const media: MediaItem[] = [];

    try {
      // TikTok embeds data in __UNIVERSAL_DATA_FOR_REHYDRATION__
      const script = await hero.document.querySelector('#__UNIVERSAL_DATA_FOR_REHYDRATION__');
      if (!script) {
        log.debug('No hydration script found');
        return media;
      }

      const text = await (script as any).textContent;
      if (!text) return media;

      const data = JSON.parse(text);
      const items = this.extractVideoNodes(data);

      for (const item of items) {
        if (!knownPostIds.has(item.postId) && !media.some(m => m.postId === item.postId)) {
          media.push(item);
        }
      }
    } catch (err) {
      log.debug({ err }, 'Hydration extraction failed');
    }

    return media;
  }

  private extractVideoNodes(obj: any, depth = 0): MediaItem[] {
    if (depth > 15 || !obj || typeof obj !== 'object') return [];

    const media: MediaItem[] = [];

    // TikTok video node structure
    if (obj.id && obj.video && (obj.video.playAddr || obj.video.downloadAddr)) {
      const videoId = String(obj.id);
      const videoUrl = obj.video.downloadAddr || obj.video.playAddr;
      const coverUrl = obj.video.cover || obj.video.originCover;

      // Video
      if (videoUrl) {
        media.push({
          postId: videoId,
          postUrl: `https://www.tiktok.com/@unknown/video/${videoId}`,
          mediaType: 'video',
          mediaUrl: videoUrl,
          caption: obj.desc,
          postedAt: obj.createTime ? new Date(obj.createTime * 1000) : undefined,
          width: obj.video?.width,
          height: obj.video?.height,
          duration: obj.video?.duration,
        });
      }

      // Cover image
      if (coverUrl) {
        media.push({
          postId: `${videoId}_cover`,
          postUrl: `https://www.tiktok.com/@unknown/video/${videoId}`,
          mediaType: 'image',
          mediaUrl: coverUrl,
          caption: obj.desc,
          postedAt: obj.createTime ? new Date(obj.createTime * 1000) : undefined,
        });
      }
    }

    // Also look for imagePost data (TikTok photo posts)
    if (obj.id && obj.imagePost && obj.imagePost.images) {
      const postId = String(obj.id);
      for (let i = 0; i < obj.imagePost.images.length; i++) {
        const img = obj.imagePost.images[i];
        const imgUrl = img.imageURL?.urlList?.[0];
        if (imgUrl) {
          media.push({
            postId: `${postId}_img${i}`,
            postUrl: `https://www.tiktok.com/@unknown/photo/${postId}`,
            mediaType: 'image',
            mediaUrl: imgUrl,
            caption: obj.desc,
            postedAt: obj.createTime ? new Date(obj.createTime * 1000) : undefined,
            width: img.imageWidth,
            height: img.imageHeight,
          });
        }
      }
    }

    // Recurse
    if (Array.isArray(obj)) {
      for (const item of obj) {
        media.push(...this.extractVideoNodes(item, depth + 1));
      }
    } else {
      for (const value of Object.values(obj)) {
        if (typeof value === 'object' && value !== null) {
          media.push(...this.extractVideoNodes(value, depth + 1));
        }
      }
    }

    return media;
  }

  private parseApiResponse(data: any): MediaItem[] {
    if (!data || typeof data !== 'object') return [];
    return this.extractVideoNodes(data);
  }
}
