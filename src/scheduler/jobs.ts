import { db } from '../db/index.js';
import { profiles, mediaItems, scrapeJobs } from '../db/schema.js';
import { eq, and, lt } from 'drizzle-orm';
import { runScrapeJob } from '../scraper/pipeline.js';
import { cleanupOldFiles } from '../storage/local.js';
import { createChildLogger } from '../utils/logger.js';
import type { Job } from './queue.js';

const log = createChildLogger('jobs');

export async function handleJob(job: Job): Promise<void> {
  switch (job.type) {
    case 'scrapeProfile':
      await handleScrapeProfile(job);
      break;
    case 'retryFailed':
      await handleRetryFailed();
      break;
    case 'cleanup':
      await handleCleanup();
      break;
    default:
      log.warn({ job }, 'Unknown job type');
  }
}

async function handleScrapeProfile(job: Job): Promise<void> {
  if (!job.jobId) {
    // Create a job record if not already created (scheduler-triggered)
    if (!job.profileId) return;

    const [newJob] = await db.insert(scrapeJobs).values({
      profileId: job.profileId,
      triggeredBy: job.triggeredBy || 'scheduler',
    }).returning();

    job.jobId = newJob.id;
  }

  await runScrapeJob(job.jobId);
}

async function handleRetryFailed(): Promise<void> {
  // Find failed media items with retryCount < 3
  const failedItems = await db.select().from(mediaItems)
    .where(and(
      eq(mediaItems.status, 'failed'),
      lt(mediaItems.retryCount, 3),
    ));

  log.info({ count: failedItems.length }, 'Retrying failed media items');

  for (const item of failedItems) {
    await db.update(mediaItems).set({
      status: 'pending',
    }).where(eq(mediaItems.id, item.id));
  }

  // Also retry downloaded-but-not-uploaded items
  const downloadedItems = await db.select().from(mediaItems)
    .where(and(
      eq(mediaItems.status, 'downloaded'),
      lt(mediaItems.retryCount, 3),
    ));

  for (const item of downloadedItems) {
    await db.update(mediaItems).set({
      status: 'pending',
    }).where(eq(mediaItems.id, item.id));
  }
}

async function handleCleanup(): Promise<void> {
  const cleaned = cleanupOldFiles();
  log.info({ cleaned }, 'Cleanup complete');
}
