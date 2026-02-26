import type Hero from '@ulixee/hero';
import type { PlatformExtractor, ExtractorResult, MediaItem, ProfileInfo, ExtractOptions } from './base.js';
import { scrollAndCollect } from '../scroller.js';
import { createChildLogger } from '../../utils/logger.js';
import { sleep } from '../../utils/retry.js';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

const log = createChildLogger('instagram');

export class InstagramExtractor implements PlatformExtractor {
  platform = 'instagram' as const;

  async extract(hero: Hero, profileUrl: string, knownPostIds: Set<string>, options?: ExtractOptions): Promise<ExtractorResult> {
    const mode = options?.scrapeMode || 'daily';
    const media: MediaItem[] = [];
    const graphqlResponses: any[] = [];
    let profileInfo: ProfileInfo = {};
    let hitKnownContent = false;
    const twoYearsAgo = new Date();
    twoYearsAgo.setFullYear(twoYearsAgo.getFullYear() - 2);
    let reachedTimeLimit = false;

    // Load session cookies if available
    await this.loadCookies(hero);

    // Intercept GraphQL API responses
    hero.activeTab.on('resource', async (event: any) => {
      try {
        const url = typeof event.url === 'string' ? event.url : String(event.url);
        if (url.includes('/graphql') || url.includes('/api/v1/users/') || url.includes('/api/v1/feed/')) {
          const response = await event.response;
          if (response?.headers?.['content-type']?.includes('json')) {
            const text = await response.text();
            try {
              const json = JSON.parse(text);
              graphqlResponses.push(json);
            } catch {}
          }
        }
      } catch (err) {
        // Resource may be unavailable, skip
      }
    });

    log.info({ profileUrl }, 'Navigating to Instagram profile');
    await hero.goto(profileUrl);
    await hero.waitForPaintingStable();
    await sleep(2000);

    // Check for login wall
    const pageText = await hero.document.body.textContent;
    if (typeof pageText === 'string' && pageText.includes('Log in')) {
      log.warn('Instagram login wall detected. Session cookies may be needed.');
    }

    // Extract profile info from meta tags or page content
    profileInfo = await this.extractProfileInfo(hero);

    // Try to extract initial media from the page's embedded JSON
    const initialMedia = await this.extractFromEmbeddedJson(hero, knownPostIds);
    for (const item of initialMedia) {
      if (knownPostIds.has(item.postId)) {
        hitKnownContent = true;
      } else {
        media.push(item);
      }
    }

    // Scroll to load more posts
    const shouldScroll = mode === 'backfill' ? !reachedTimeLimit : !hitKnownContent;
    if (shouldScroll) {
      await scrollAndCollect(hero, {
        maxScrolls: options?.maxScrolls,
        shouldStop: () => mode === 'backfill' ? reachedTimeLimit : hitKnownContent,
        onScroll: () => {
          // Process any new GraphQL responses
          while (graphqlResponses.length > 0) {
            const response = graphqlResponses.shift();
            const items = this.parseGraphqlResponse(response);
            for (const item of items) {
              // In backfill mode, check if we've gone past 2 years
              if (mode === 'backfill' && item.postedAt && item.postedAt < twoYearsAgo) {
                reachedTimeLimit = true;
              }
              if (knownPostIds.has(item.postId)) {
                if (mode === 'daily') hitKnownContent = true;
              } else if (!media.some(m => m.postId === item.postId && m.mediaUrl === item.mediaUrl)) {
                media.push(item);
              }
            }
          }
        },
      });
    }

    // Process remaining GraphQL responses
    while (graphqlResponses.length > 0) {
      const response = graphqlResponses.shift();
      const items = this.parseGraphqlResponse(response);
      for (const item of items) {
        if (!knownPostIds.has(item.postId) && !media.some(m => m.postId === item.postId && m.mediaUrl === item.mediaUrl)) {
          media.push(item);
        }
      }
    }

    // Fallback: extract from DOM if GraphQL interception yielded nothing
    if (media.length === 0) {
      const domMedia = await this.extractFromDom(hero, profileUrl, knownPostIds);
      media.push(...domMedia);
    }

    log.info({ mediaCount: media.length }, 'Instagram extraction complete');
    return { profileInfo, media };
  }

  private async loadCookies(hero: Hero): Promise<void> {
    const cookiePath = resolve('data/sessions/instagram.json');
    if (!existsSync(cookiePath)) {
      log.info('No Instagram session cookies found');
      return;
    }

    try {
      const cookiesRaw = readFileSync(cookiePath, 'utf-8');
      const cookies = JSON.parse(cookiesRaw);

      if (Array.isArray(cookies)) {
        for (const cookie of cookies) {
          try {
            await hero.activeTab.cookieStorage.setItem(cookie.name, cookie.value, {
              domain: cookie.domain || '.instagram.com',
              path: cookie.path || '/',
              secure: cookie.secure ?? true,
              httpOnly: cookie.httpOnly ?? false,
              expires: cookie.expirationDate ? new Date(cookie.expirationDate * 1000).toUTCString() : undefined,
            });
          } catch {}
        }
        log.info({ count: cookies.length }, 'Instagram cookies loaded');
      }
    } catch (err) {
      log.error({ err }, 'Failed to load Instagram cookies');
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

  private async extractFromEmbeddedJson(hero: Hero, knownPostIds: Set<string>): Promise<MediaItem[]> {
    const media: MediaItem[] = [];

    try {
      // Look for embedded JSON data in script tags
      const scripts = await hero.document.querySelectorAll('script[type="application/json"]');
      for (const script of scripts) {
        try {
          const text = await (script as any).textContent;
          if (!text || text.length < 100) continue;

          const data = JSON.parse(text);
          const items = this.extractMediaFromJsonTree(data);
          for (const item of items) {
            if (!knownPostIds.has(item.postId) && !media.some(m => m.postId === item.postId && m.mediaUrl === item.mediaUrl)) {
              media.push(item);
            }
          }
        } catch {}
      }
    } catch (err) {
      log.debug({ err }, 'No embedded JSON data found');
    }

    return media;
  }

  private extractMediaFromJsonTree(obj: any, depth = 0): MediaItem[] {
    if (depth > 15 || !obj || typeof obj !== 'object') return [];

    const media: MediaItem[] = [];

    // Look for Instagram media node structures
    if (obj.shortcode && (obj.display_url || obj.video_url)) {
      const postUrl = `https://www.instagram.com/p/${obj.shortcode}/`;

      if (obj.video_url) {
        media.push({
          postId: obj.shortcode,
          postUrl,
          mediaType: 'video',
          mediaUrl: obj.video_url,
          caption: obj.edge_media_to_caption?.edges?.[0]?.node?.text,
          postedAt: obj.taken_at_timestamp ? new Date(obj.taken_at_timestamp * 1000) : undefined,
          width: obj.dimensions?.width,
          height: obj.dimensions?.height,
          duration: obj.video_duration,
        });
      }

      if (obj.display_url) {
        media.push({
          postId: obj.shortcode,
          postUrl,
          mediaType: 'image',
          mediaUrl: obj.display_url,
          caption: obj.edge_media_to_caption?.edges?.[0]?.node?.text,
          postedAt: obj.taken_at_timestamp ? new Date(obj.taken_at_timestamp * 1000) : undefined,
          width: obj.dimensions?.width,
          height: obj.dimensions?.height,
        });
      }

      // Handle carousel (sidecar) posts
      if (obj.edge_sidecar_to_children?.edges) {
        for (const edge of obj.edge_sidecar_to_children.edges) {
          const child = edge.node;
          if (!child) continue;

          if (child.video_url) {
            media.push({
              postId: `${obj.shortcode}_${child.id || media.length}`,
              postUrl,
              mediaType: 'video',
              mediaUrl: child.video_url,
              caption: obj.edge_media_to_caption?.edges?.[0]?.node?.text,
              postedAt: obj.taken_at_timestamp ? new Date(obj.taken_at_timestamp * 1000) : undefined,
              width: child.dimensions?.width,
              height: child.dimensions?.height,
            });
          } else if (child.display_url) {
            media.push({
              postId: `${obj.shortcode}_${child.id || media.length}`,
              postUrl,
              mediaType: 'image',
              mediaUrl: child.display_url,
              caption: obj.edge_media_to_caption?.edges?.[0]?.node?.text,
              postedAt: obj.taken_at_timestamp ? new Date(obj.taken_at_timestamp * 1000) : undefined,
              width: child.dimensions?.width,
              height: child.dimensions?.height,
            });
          }
        }
      }
    }

    // Recurse into object/array children
    if (Array.isArray(obj)) {
      for (const item of obj) {
        media.push(...this.extractMediaFromJsonTree(item, depth + 1));
      }
    } else {
      for (const value of Object.values(obj)) {
        if (typeof value === 'object' && value !== null) {
          media.push(...this.extractMediaFromJsonTree(value, depth + 1));
        }
      }
    }

    return media;
  }

  private parseGraphqlResponse(data: any): MediaItem[] {
    if (!data || typeof data !== 'object') return [];
    return this.extractMediaFromJsonTree(data);
  }

  private async extractFromDom(hero: Hero, profileUrl: string, knownPostIds: Set<string>): Promise<MediaItem[]> {
    const media: MediaItem[] = [];

    try {
      // Extract image URLs from post thumbnails on the profile grid
      const links = await hero.document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');

      for (const link of links) {
        try {
          const href = await (link as any).getAttribute('href');
          if (!href) continue;

          // Extract shortcode from URL
          const match = href.match(/\/(p|reel)\/([^/]+)/);
          if (!match) continue;

          const shortcode = match[2];
          if (knownPostIds.has(shortcode)) continue;

          // Find images within the link
          const img = await (link as any).querySelector('img');
          if (img) {
            const src = await img.getAttribute('src');
            if (src) {
              media.push({
                postId: shortcode,
                postUrl: `https://www.instagram.com${href}`,
                mediaType: href.includes('/reel/') ? 'video' : 'image',
                mediaUrl: src,
              });
            }
          }
        } catch {}
      }
    } catch (err) {
      log.debug({ err }, 'DOM extraction failed');
    }

    return media;
  }
}
