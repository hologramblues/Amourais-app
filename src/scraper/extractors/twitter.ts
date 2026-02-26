import type Hero from '@ulixee/hero';
import type { PlatformExtractor, ExtractorResult, MediaItem, ProfileInfo, ExtractOptions } from './base.js';
import { scrollAndCollect } from '../scroller.js';
import { createChildLogger } from '../../utils/logger.js';
import { sleep } from '../../utils/retry.js';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

const log = createChildLogger('twitter');

export class TwitterExtractor implements PlatformExtractor {
  platform = 'twitter' as const;

  async extract(hero: Hero, profileUrl: string, knownPostIds: Set<string>, options?: ExtractOptions): Promise<ExtractorResult> {
    const mode = options?.scrapeMode || 'daily';
    const media: MediaItem[] = [];
    const graphqlResponses: any[] = [];
    let profileInfo: ProfileInfo = {};
    let hitKnownContent = false;
    const twoYearsAgo = new Date();
    twoYearsAgo.setFullYear(twoYearsAgo.getFullYear() - 2);
    let reachedTimeLimit = false;

    // Load session cookies
    await this.loadCookies(hero);

    // Intercept GraphQL API responses
    hero.activeTab.on('resource', async (event: any) => {
      try {
        const url = typeof event.url === 'string' ? event.url : String(event.url);
        if (url.includes('/i/api/graphql/') || url.includes('/UserMedia') || url.includes('/UserTweets')) {
          const response = await event.response;
          if (response?.headers?.['content-type']?.includes('json')) {
            const text = await response.text();
            try {
              graphqlResponses.push(JSON.parse(text));
            } catch {}
          }
        }
      } catch {}
    });

    // Navigate to media tab
    const mediaUrl = profileUrl.endsWith('/media') ? profileUrl : `${profileUrl.replace(/\/$/, '')}/media`;
    log.info({ mediaUrl }, 'Navigating to Twitter media tab');
    await hero.goto(mediaUrl);
    await hero.waitForPaintingStable();
    await sleep(3000);

    // Extract profile info
    profileInfo = await this.extractProfileInfo(hero);

    // Process initial GraphQL responses
    await sleep(2000);
    this.processResponses(graphqlResponses, media, knownPostIds, (type, item) => {
      if (type === 'known' && mode === 'daily') hitKnownContent = true;
      if (type === 'timecheck' && mode === 'backfill' && item.postedAt && item.postedAt < twoYearsAgo) reachedTimeLimit = true;
    });

    // Scroll for more content
    const shouldScroll = mode === 'backfill' ? !reachedTimeLimit : !hitKnownContent;
    if (shouldScroll) {
      await scrollAndCollect(hero, {
        maxScrolls: options?.maxScrolls || 20,
        scrollPauseMs: 3000,
        shouldStop: () => mode === 'backfill' ? reachedTimeLimit : hitKnownContent,
        onScroll: () => {
          this.processResponses(graphqlResponses, media, knownPostIds, (type, item) => {
            if (type === 'known' && mode === 'daily') hitKnownContent = true;
            if (type === 'timecheck' && mode === 'backfill' && item.postedAt && item.postedAt < twoYearsAgo) reachedTimeLimit = true;
          });
        },
      });
    }

    // Process remaining responses
    this.processResponses(graphqlResponses, media, knownPostIds, () => {});

    log.info({ mediaCount: media.length }, 'Twitter extraction complete');
    return { profileInfo, media };
  }

  private async loadCookies(hero: Hero): Promise<void> {
    const cookiePath = resolve('data/sessions/twitter.json');
    if (!existsSync(cookiePath)) return;

    try {
      const cookiesRaw = readFileSync(cookiePath, 'utf-8');
      const cookies = JSON.parse(cookiesRaw);
      if (Array.isArray(cookies)) {
        for (const cookie of cookies) {
          try {
            await hero.activeTab.cookieStorage.setItem(cookie.name, cookie.value, {
              domain: cookie.domain || '.x.com',
              path: cookie.path || '/',
              secure: cookie.secure ?? true,
              httpOnly: cookie.httpOnly ?? false,
            });
          } catch {}
        }
        log.info({ count: cookies.length }, 'Twitter cookies loaded');
      }
    } catch (err) {
      log.error({ err }, 'Failed to load Twitter cookies');
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

  private processResponses(
    responses: any[],
    media: MediaItem[],
    knownPostIds: Set<string>,
    onHit: (type: 'known' | 'timecheck', item: MediaItem) => void,
  ): void {
    while (responses.length > 0) {
      const response = responses.shift();
      const items = this.extractTweetsFromGraphql(response);
      for (const item of items) {
        if (knownPostIds.has(item.postId)) {
          onHit('known', item);
        } else if (!media.some(m => m.postId === item.postId && m.mediaUrl === item.mediaUrl)) {
          media.push(item);
        }
        // Always check time for backfill
        onHit('timecheck', item);
      }
    }
  }

  private extractTweetsFromGraphql(obj: any, depth = 0): MediaItem[] {
    if (depth > 20 || !obj || typeof obj !== 'object') return [];

    const media: MediaItem[] = [];

    // Twitter tweet_results structure
    if (obj.rest_id && obj.legacy?.entities?.media) {
      const tweetId = String(obj.rest_id);
      const tweetUrl = `https://x.com/i/status/${tweetId}`;
      const caption = obj.legacy.full_text;
      const createdAt = obj.legacy.created_at ? new Date(obj.legacy.created_at) : undefined;

      for (const m of obj.legacy.entities.media) {
        if (m.type === 'photo') {
          // Append :orig for full resolution
          const imageUrl = m.media_url_https ? `${m.media_url_https}:orig` : m.media_url_https;
          media.push({
            postId: `${tweetId}_${m.media_key || media.length}`,
            postUrl: tweetUrl,
            mediaType: 'image',
            mediaUrl: imageUrl,
            caption,
            postedAt: createdAt,
            width: m.original_info?.width,
            height: m.original_info?.height,
          });
        }
      }

      // Check extended_entities for videos
      if (obj.legacy.extended_entities?.media) {
        for (const m of obj.legacy.extended_entities.media) {
          if (m.type === 'video' || m.type === 'animated_gif') {
            const videoUrl = this.getBestVideoVariant(m.video_info?.variants);
            if (videoUrl) {
              media.push({
                postId: `${tweetId}_${m.media_key || media.length}`,
                postUrl: tweetUrl,
                mediaType: 'video',
                mediaUrl: videoUrl,
                caption,
                postedAt: createdAt,
                width: m.original_info?.width,
                height: m.original_info?.height,
                duration: m.video_info?.duration_millis ? m.video_info.duration_millis / 1000 : undefined,
              });
            }
          }
        }
      }
    }

    // Recurse into object/array children
    if (Array.isArray(obj)) {
      for (const item of obj) {
        media.push(...this.extractTweetsFromGraphql(item, depth + 1));
      }
    } else {
      for (const value of Object.values(obj)) {
        if (typeof value === 'object' && value !== null) {
          media.push(...this.extractTweetsFromGraphql(value, depth + 1));
        }
      }
    }

    return media;
  }

  private getBestVideoVariant(variants?: Array<{ bitrate?: number; url: string; content_type?: string }>): string | null {
    if (!variants || variants.length === 0) return null;

    const mp4Variants = variants.filter(v => v.content_type === 'video/mp4' && v.bitrate !== undefined);

    if (mp4Variants.length === 0) {
      // Fallback to any variant
      return variants[0]?.url || null;
    }

    // Sort by bitrate descending, pick highest quality
    mp4Variants.sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0));
    return mp4Variants[0].url;
  }
}
