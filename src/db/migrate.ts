import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import { db } from './index.js';
import { createChildLogger } from '../utils/logger.js';

const log = createChildLogger('migrate');

log.info('Running migrations...');
migrate(db, { migrationsFolder: './src/db/migrations' });
log.info('Migrations complete');
