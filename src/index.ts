import { config } from './config/index.js';
import { createChildLogger } from './utils/logger.js';
import { db } from './db/index.js';
import { startServer } from './web/server.js';
import { startScheduler } from './scheduler/index.js';
import { shutdownPool } from './scraper/pool.js';
import { mkdirSync } from 'node:fs';

const log = createChildLogger('main');

async function main() {
  log.info('Starting SAMOURAIS SCRAPPER...');

  // Ensure directories exist
  mkdirSync(config.paths.downloadDir, { recursive: true });
  mkdirSync('data/sessions', { recursive: true });

  // Run migrations inline (create tables if they don't exist)
  const { migrate } = await import('drizzle-orm/better-sqlite3/migrator');
  try {
    migrate(db, { migrationsFolder: './src/db/migrations' });
    log.info('Database migrations applied');
  } catch (err) {
    log.warn({ err }, 'Migration skipped (may need to generate first)');
  }

  // Start web server
  startServer();

  // Start scheduler (cron jobs)
  startScheduler();

  log.info('SAMOURAIS SCRAPPER is ready');

  // Graceful shutdown
  const shutdown = async () => {
    log.info('Shutting down...');
    await shutdownPool();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

main().catch((err) => {
  log.fatal({ err }, 'Failed to start');
  process.exit(1);
});
