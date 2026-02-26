import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { config } from '../config/index.js';
import * as schema from './schema.js';
import { createChildLogger } from '../utils/logger.js';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

const log = createChildLogger('db');

mkdirSync(dirname(config.paths.dbPath), { recursive: true });

const sqlite = new Database(config.paths.dbPath);
sqlite.pragma('journal_mode = WAL');
sqlite.pragma('foreign_keys = ON');

export const db = drizzle(sqlite, { schema });

log.info({ path: config.paths.dbPath }, 'Database connected');
