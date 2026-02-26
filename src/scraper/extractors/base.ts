import type Hero from '@ulixee/hero';

export interface MediaItem {
  postId: string;
  postUrl: string;
  mediaType: 'image' | 'video';
  mediaUrl: string;
  caption?: string;
  postedAt?: Date;
  width?: number;
  height?: number;
  duration?: number;
}

export interface ProfileInfo {
  displayName?: string;
  avatarUrl?: string;
}

export interface ExtractOptions {
  scrapeMode: 'backfill' | 'daily';
  maxScrolls?: number;
}

export interface ExtractorResult {
  profileInfo: ProfileInfo;
  media: MediaItem[];
}

export interface PlatformExtractor {
  platform: 'instagram' | 'tiktok' | 'twitter';
  extract(hero: Hero, profileUrl: string, knownPostIds: Set<string>, options?: ExtractOptions): Promise<ExtractorResult>;
}
