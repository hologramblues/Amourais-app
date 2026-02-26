import { sqliteTable, text, integer, real, uniqueIndex, index } from 'drizzle-orm/sqlite-core';

export const profiles = sqliteTable('profiles', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  platform: text('platform').notNull(), // 'instagram' | 'tiktok' | 'twitter'
  username: text('username').notNull(),
  profileUrl: text('profile_url').notNull(),
  displayName: text('display_name'),
  avatarUrl: text('avatar_url'),
  isActive: integer('is_active', { mode: 'boolean' }).notNull().default(true),
  scrapeMode: text('scrape_mode').notNull().default('backfill'), // 'backfill' | 'daily'
  scrapeIntervalMinutes: integer('scrape_interval_minutes').notNull().default(360),
  lastScrapedAt: integer('last_scraped_at', { mode: 'timestamp' }),
  gdriveFolderId: text('gdrive_folder_id'),
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull().$defaultFn(() => new Date()),
  updatedAt: integer('updated_at', { mode: 'timestamp' }).notNull().$defaultFn(() => new Date()),
}, (table) => [
  uniqueIndex('idx_profiles_platform_username').on(table.platform, table.username),
]);

export const mediaItems = sqliteTable('media_items', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  profileId: integer('profile_id').notNull().references(() => profiles.id, { onDelete: 'cascade' }),
  platform: text('platform').notNull(),
  postId: text('post_id').notNull(),
  postUrl: text('post_url'),
  mediaType: text('media_type').notNull(), // 'image' | 'video'
  mediaUrl: text('media_url').notNull(),
  contentHash: text('content_hash'),
  fileSize: integer('file_size'),
  width: integer('width'),
  height: integer('height'),
  duration: real('duration'),
  caption: text('caption'),
  postedAt: integer('posted_at', { mode: 'timestamp' }),
  status: text('status').notNull().default('pending'), // pending | downloading | downloaded | uploading | uploaded | failed
  localPath: text('local_path'),
  gdriveFileId: text('gdrive_file_id'),
  gdriveUrl: text('gdrive_url'),
  errorMessage: text('error_message'),
  retryCount: integer('retry_count').notNull().default(0),
  discoveredAt: integer('discovered_at', { mode: 'timestamp' }).notNull().$defaultFn(() => new Date()),
  downloadedAt: integer('downloaded_at', { mode: 'timestamp' }),
  uploadedAt: integer('uploaded_at', { mode: 'timestamp' }),
}, (table) => [
  uniqueIndex('idx_media_dedup').on(table.profileId, table.postId, table.mediaUrl),
  index('idx_media_status').on(table.status),
  index('idx_media_profile').on(table.profileId),
]);

export const scrapeJobs = sqliteTable('scrape_jobs', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  profileId: integer('profile_id').notNull().references(() => profiles.id, { onDelete: 'cascade' }),
  status: text('status').notNull().default('queued'), // queued | running | completed | failed | partial
  triggeredBy: text('triggered_by').notNull(), // 'scheduler' | 'manual'
  mediaFound: integer('media_found').notNull().default(0),
  mediaNew: integer('media_new').notNull().default(0),
  mediaDownloaded: integer('media_downloaded').notNull().default(0),
  mediaUploaded: integer('media_uploaded').notNull().default(0),
  errorMessage: text('error_message'),
  startedAt: integer('started_at', { mode: 'timestamp' }),
  completedAt: integer('completed_at', { mode: 'timestamp' }),
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull().$defaultFn(() => new Date()),
}, (table) => [
  index('idx_jobs_profile').on(table.profileId),
  index('idx_jobs_status').on(table.status),
]);

export type Platform = 'instagram' | 'tiktok' | 'twitter';
export type MediaType = 'image' | 'video';
export type MediaStatus = 'pending' | 'downloading' | 'downloaded' | 'uploading' | 'uploaded' | 'failed';
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'partial';
export type ScrapeMode = 'backfill' | 'daily';
