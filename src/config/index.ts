import 'dotenv/config';
import { resolve } from 'node:path';

export const config = {
  port: parseInt(process.env.PORT || '3000', 10),
  nodeEnv: process.env.NODE_ENV || 'development',
  isDev: (process.env.NODE_ENV || 'development') === 'development',

  google: {
    clientId: process.env.GOOGLE_CLIENT_ID || '',
    clientSecret: process.env.GOOGLE_CLIENT_SECRET || '',
    redirectUri: process.env.GOOGLE_REDIRECT_URI || 'http://localhost:3000/auth/google/callback',
    refreshToken: process.env.GOOGLE_REFRESH_TOKEN || '',
  },

  gdrive: {
    rootFolderName: process.env.GDRIVE_ROOT_FOLDER_NAME || 'SAMOURAIS SCRAPPER',
  },

  scraper: {
    browserPoolSize: parseInt(process.env.BROWSER_POOL_SIZE || '2', 10),
    defaultScrapeIntervalMinutes: parseInt(process.env.DEFAULT_SCRAPE_INTERVAL_MINUTES || '360', 10),
    scrollPauseMs: parseInt(process.env.SCROLL_PAUSE_MS || '3000', 10),
    maxScrolls: parseInt(process.env.MAX_SCROLLS || '30', 10),
    backfillMaxScrolls: parseInt(process.env.BACKFILL_MAX_SCROLLS || '200', 10),
    dailyMaxScrolls: parseInt(process.env.DAILY_MAX_SCROLLS || '15', 10),
    dailyScrapeIntervalMinutes: parseInt(process.env.DAILY_SCRAPE_INTERVAL_MINUTES || '1440', 10),
    delayBetweenProfilesMs: parseInt(process.env.DELAY_BETWEEN_PROFILES_MS || '10000', 10),
  },

  paths: {
    downloadDir: resolve(process.env.DOWNLOAD_DIR || './data/downloads'),
    dbPath: resolve(process.env.DB_PATH || './data/samourais.db'),
  },

  logLevel: process.env.LOG_LEVEL || 'info',
} as const;
