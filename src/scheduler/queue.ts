import { createChildLogger } from '../utils/logger.js';
import { sleep } from '../utils/retry.js';
import { config } from '../config/index.js';

const log = createChildLogger('queue');

export interface Job {
  type: 'scrapeProfile' | 'retryFailed' | 'cleanup';
  profileId?: number;
  jobId?: number;
  triggeredBy?: string;
}

type JobHandler = (job: Job) => Promise<void>;

export class SimpleQueue {
  private queue: Job[] = [];
  private processing = false;
  private handler: JobHandler;

  constructor(handler: JobHandler) {
    this.handler = handler;
  }

  enqueue(job: Job): void {
    // Prevent duplicate profile scrape jobs
    if (job.type === 'scrapeProfile' && job.profileId) {
      const exists = this.queue.some(
        j => j.type === 'scrapeProfile' && j.profileId === job.profileId,
      );
      if (exists) {
        log.debug({ profileId: job.profileId }, 'Duplicate scrape job skipped');
        return;
      }
    }

    this.queue.push(job);
    log.info({ job, queueLength: this.queue.length }, 'Job enqueued');
    this.process();
  }

  get length(): number {
    return this.queue.length;
  }

  get isProcessing(): boolean {
    return this.processing;
  }

  private async process(): Promise<void> {
    if (this.processing) return;
    this.processing = true;

    while (this.queue.length > 0) {
      const job = this.queue.shift()!;
      log.info({ job, remaining: this.queue.length }, 'Processing job');

      try {
        await this.handler(job);
      } catch (err) {
        log.error({ err, job }, 'Job processing failed');
      }

      // Delay between jobs to avoid rate limiting
      if (this.queue.length > 0) {
        await sleep(config.scraper.delayBetweenProfilesMs);
      }
    }

    this.processing = false;
  }
}
