import cron from 'node-cron';
import { db } from '../db/index.js';
import { profiles } from '../db/schema.js';
import { eq, or, isNull, sql } from 'drizzle-orm';
import { SimpleQueue } from './queue.js';
import { handleJob } from './jobs.js';
import { createChildLogger } from '../utils/logger.js';

const log = createChildLogger('scheduler');

export const jobQueue = new SimpleQueue(handleJob);

export function startScheduler(): void {
  log.info('Starting scheduler');

  // Check for due profiles every 30 minutes
  cron.schedule('*/30 * * * *', async () => {
    log.info('Checking for profiles due for scraping');

    try {
      const dueProfiles = await db.select().from(profiles)
        .where(
          eq(profiles.isActive, true),
        );

      const now = Date.now();

      for (const profile of dueProfiles) {
        const lastScraped = profile.lastScrapedAt ? profile.lastScrapedAt.getTime() : 0;
        const intervalMs = profile.scrapeIntervalMinutes * 60 * 1000;

        if (now - lastScraped >= intervalMs) {
          log.info({ username: profile.username, platform: profile.platform }, 'Profile due for scrape');
          jobQueue.enqueue({
            type: 'scrapeProfile',
            profileId: profile.id,
            triggeredBy: 'scheduler',
          });
        }
      }
    } catch (err) {
      log.error({ err }, 'Failed to check due profiles');
    }
  });

  // Retry failed items every 2 hours
  cron.schedule('0 */2 * * *', () => {
    log.info('Scheduling retry of failed items');
    jobQueue.enqueue({ type: 'retryFailed' });
  });

  // Cleanup old temp files daily at 3 AM
  cron.schedule('0 3 * * *', () => {
    log.info('Scheduling cleanup');
    jobQueue.enqueue({ type: 'cleanup' });
  });

  log.info('Scheduler started with cron jobs');
}

export function enqueueManualScrape(profileId: number, jobId: number): void {
  jobQueue.enqueue({
    type: 'scrapeProfile',
    profileId,
    jobId,
    triggeredBy: 'manual',
  });
}
