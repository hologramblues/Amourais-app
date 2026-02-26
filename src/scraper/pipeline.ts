import { db } from '../db/index.js';
import { profiles, mediaItems, scrapeJobs, type Platform, type ScrapeMode } from '../db/schema.js';
import { eq, and, inArray } from 'drizzle-orm';
import { getPool } from './pool.js';
import { download } from './downloaders/index.js';
import { hashFileStream } from '../utils/hash.js';
import { createChildLogger } from '../utils/logger.js';
import { sleep } from '../utils/retry.js';
import { config } from '../config/index.js';
import { unlinkSync, existsSync } from 'node:fs';
import type { PlatformExtractor } from './extractors/base.js';
import { InstagramExtractor } from './extractors/instagram.js';
import { TikTokExtractor } from './extractors/tiktok.js';
import { TwitterExtractor } from './extractors/twitter.js';

const log = createChildLogger('pipeline');

const extractors: Record<Platform, PlatformExtractor> = {
  instagram: new InstagramExtractor(),
  tiktok: new TikTokExtractor(),
  twitter: new TwitterExtractor(),
};

export async function runScrapeJob(jobId: number): Promise<void> {
  const [job] = await db.select().from(scrapeJobs).where(eq(scrapeJobs.id, jobId));
  if (!job) throw new Error(`Job ${jobId} not found`);

  const [profile] = await db.select().from(profiles).where(eq(profiles.id, job.profileId));
  if (!profile) throw new Error(`Profile ${job.profileId} not found`);

  const extractor = extractors[profile.platform as Platform];
  if (!extractor) {
    await db.update(scrapeJobs).set({
      status: 'failed',
      errorMessage: `Extractor not implemented for ${profile.platform}`,
      completedAt: new Date(),
    }).where(eq(scrapeJobs.id, jobId));
    return;
  }

  // Mark job as running
  await db.update(scrapeJobs).set({
    status: 'running',
    startedAt: new Date(),
  }).where(eq(scrapeJobs.id, jobId));

  const scrapeMode = (profile.scrapeMode || 'daily') as ScrapeMode;
  const maxScrolls = scrapeMode === 'backfill'
    ? config.scraper.backfillMaxScrolls
    : config.scraper.dailyMaxScrolls;

  log.info({ jobId, profile: profile.username, platform: profile.platform, scrapeMode }, 'Starting scrape job');

  try {
    const pool = await getPool();

    // Get known post IDs for deduplication
    const existingMedia = await db.select({ postId: mediaItems.postId })
      .from(mediaItems)
      .where(eq(mediaItems.profileId, profile.id));
    const knownPostIds = new Set(existingMedia.map(m => m.postId));

    // Run extraction inside a browser session
    const result = await pool.withBrowser(async (hero) => {
      return extractor.extract(hero, profile.profileUrl, knownPostIds, {
        scrapeMode,
        maxScrolls,
      });
    });

    const mediaFound = result.media.length;
    log.info({ jobId, mediaFound }, 'Extraction complete');

    // Update profile info if available
    if (result.profileInfo.displayName || result.profileInfo.avatarUrl) {
      await db.update(profiles).set({
        displayName: result.profileInfo.displayName || profile.displayName,
        avatarUrl: result.profileInfo.avatarUrl || profile.avatarUrl,
        updatedAt: new Date(),
      }).where(eq(profiles.id, profile.id));
    }

    // Insert new media items (skip duplicates)
    let mediaNew = 0;
    for (const item of result.media) {
      try {
        await db.insert(mediaItems).values({
          profileId: profile.id,
          platform: profile.platform,
          postId: item.postId,
          postUrl: item.postUrl,
          mediaType: item.mediaType,
          mediaUrl: item.mediaUrl,
          caption: item.caption,
          postedAt: item.postedAt,
          width: item.width,
          height: item.height,
          duration: item.duration,
          status: 'pending',
        }).onConflictDoNothing();
        mediaNew++;
      } catch (err: any) {
        if (!err.message?.includes('UNIQUE')) {
          log.error({ err, postId: item.postId }, 'Failed to insert media item');
        }
      }
    }

    await db.update(scrapeJobs).set({
      mediaFound,
      mediaNew,
    }).where(eq(scrapeJobs.id, jobId));

    // Download and process new pending media
    const pendingMedia = await db.select().from(mediaItems)
      .where(and(
        eq(mediaItems.profileId, profile.id),
        eq(mediaItems.status, 'pending'),
      ));

    let mediaDownloaded = 0;
    let mediaUploaded = 0;

    for (const item of pendingMedia) {
      try {
        // Download
        await db.update(mediaItems).set({ status: 'downloading' }).where(eq(mediaItems.id, item.id));

        const downloadResult = await download(item.mediaUrl);
        const contentHash = await hashFileStream(downloadResult.localPath);

        await db.update(mediaItems).set({
          status: 'downloaded',
          localPath: downloadResult.localPath,
          fileSize: downloadResult.fileSize,
          contentHash,
          downloadedAt: new Date(),
        }).where(eq(mediaItems.id, item.id));

        mediaDownloaded++;

        // Upload to Google Drive (if configured)
        try {
          const { uploadToGDrive } = await import('../storage/gdrive.js');
          const uploadResult = await uploadToGDrive(
            downloadResult.localPath,
            profile.platform as Platform,
            profile.username,
            item.postId,
            downloadResult.mimeType,
          );

          await db.update(mediaItems).set({
            status: 'uploaded',
            gdriveFileId: uploadResult.fileId,
            gdriveUrl: uploadResult.webViewLink,
            uploadedAt: new Date(),
          }).where(eq(mediaItems.id, item.id));

          mediaUploaded++;

          // Clean up local file
          if (existsSync(downloadResult.localPath)) {
            unlinkSync(downloadResult.localPath);
          }
        } catch (uploadErr) {
          log.warn({ err: uploadErr, mediaId: item.id }, 'Google Drive upload failed, keeping local file');
          // Mark as downloaded but not uploaded - will be retried
          await db.update(mediaItems).set({
            status: 'downloaded',
            errorMessage: `Upload failed: ${uploadErr}`,
          }).where(eq(mediaItems.id, item.id));
        }

        // Rate limit between downloads
        await sleep(1000);
      } catch (err) {
        log.error({ err, mediaId: item.id }, 'Failed to process media item');
        await db.update(mediaItems).set({
          status: 'failed',
          errorMessage: String(err),
          retryCount: item.retryCount + 1,
        }).where(eq(mediaItems.id, item.id));
      }
    }

    // Update job stats
    await db.update(scrapeJobs).set({
      status: 'completed',
      mediaDownloaded,
      mediaUploaded,
      completedAt: new Date(),
    }).where(eq(scrapeJobs.id, jobId));

    // Update profile last scraped
    const profileUpdates: Record<string, unknown> = {
      lastScrapedAt: new Date(),
      updatedAt: new Date(),
    };

    // Auto-transition: backfill → daily when no new media found
    if (scrapeMode === 'backfill' && mediaNew === 0) {
      profileUpdates.scrapeMode = 'daily';
      profileUpdates.scrapeIntervalMinutes = config.scraper.dailyScrapeIntervalMinutes;
      log.info({ profileId: profile.id, username: profile.username }, 'Backfill complete, switching to daily mode');
    }

    await db.update(profiles).set(profileUpdates).where(eq(profiles.id, profile.id));

    log.info({
      jobId,
      scrapeMode,
      mediaFound,
      mediaNew,
      mediaDownloaded,
      mediaUploaded,
    }, 'Scrape job completed');

  } catch (err) {
    log.error({ err, jobId }, 'Scrape job failed');
    await db.update(scrapeJobs).set({
      status: 'failed',
      errorMessage: String(err),
      completedAt: new Date(),
    }).where(eq(scrapeJobs.id, jobId));
  }
}
