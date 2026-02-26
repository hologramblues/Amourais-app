import { BrowserPool } from '@vakra-dev/reader';
import { config } from '../config/index.js';
import { createChildLogger } from '../utils/logger.js';

const log = createChildLogger('browser-pool');

let pool: BrowserPool | null = null;

export async function getPool(): Promise<BrowserPool> {
  if (pool) return pool;

  log.info({ poolSize: config.scraper.browserPoolSize }, 'Initializing browser pool');

  pool = new BrowserPool(
    {
      size: config.scraper.browserPoolSize,
      retireAfterPageCount: 50,
      retireAfterAgeMs: 30 * 60 * 1000,
    },
    undefined, // proxy config - can be added later
    false,     // showChrome
  );

  await pool.initialize();
  log.info('Browser pool initialized');

  return pool;
}

export async function shutdownPool(): Promise<void> {
  if (!pool) return;
  log.info('Shutting down browser pool');
  await pool.shutdown();
  pool = null;
}

export async function getPoolStats() {
  if (!pool) return null;
  return pool.getStats();
}

export async function poolHealthCheck() {
  if (!pool) return { healthy: false, reason: 'Pool not initialized' };
  try {
    const health = await pool.healthCheck();
    return health;
  } catch (err) {
    return { healthy: false, reason: String(err) };
  }
}
