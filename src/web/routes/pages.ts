import { Router } from 'express';
import { db } from '../../db/index.js';
import { profiles, mediaItems, scrapeJobs } from '../../db/schema.js';
import { eq, sql, count, and } from 'drizzle-orm';
import { config } from '../../config/index.js';
import { getGDriveAuthUrl, exchangeCode } from '../../storage/gdrive.js';
import { readEnvFile } from '../../utils/env-editor.js';

export const pagesRouter = Router();

pagesRouter.get('/', async (_req, res) => {
  const allProfiles = await db.select().from(profiles).all();

  const [totalMediaRow] = await db.select({ count: count() }).from(mediaItems);
  const [uploadedMediaRow] = await db.select({ count: count() }).from(mediaItems).where(eq(mediaItems.status, 'uploaded'));
  const [pendingMediaRow] = await db.select({ count: count() }).from(mediaItems).where(eq(mediaItems.status, 'pending'));

  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayTimestamp = Math.floor(todayStart.getTime() / 1000);
  const [todayJobsRow] = await db.select({ count: count() }).from(scrapeJobs)
    .where(sql`${scrapeJobs.createdAt} >= ${todayTimestamp}`);
  const [runningJobsRow] = await db.select({ count: count() }).from(scrapeJobs)
    .where(eq(scrapeJobs.status, 'running'));

  res.render('dashboard', {
    page: 'dashboard',
    profiles: allProfiles,
    stats: {
      totalProfiles: allProfiles.length,
      activeProfiles: allProfiles.filter(p => p.isActive).length,
      totalMedia: totalMediaRow.count,
      uploadedMedia: uploadedMediaRow.count,
      pendingMedia: pendingMediaRow.count,
      todayJobs: todayJobsRow.count,
      runningJobs: runningJobsRow.count,
    },
  });
});

pagesRouter.get('/profiles', async (_req, res) => {
  const allProfiles = await db.select().from(profiles).all();

  const profilesWithCounts = await Promise.all(
    allProfiles.map(async (profile) => {
      const [mediaCount] = await db.select({ count: count() }).from(mediaItems)
        .where(eq(mediaItems.profileId, profile.id));
      return { ...profile, mediaCount: mediaCount.count };
    }),
  );

  res.render('profiles', {
    page: 'profiles',
    profiles: profilesWithCounts,
  });
});

pagesRouter.get('/jobs', async (_req, res) => {
  res.render('jobs', { page: 'jobs' });
});

pagesRouter.get('/settings', async (_req, res) => {
  const gdriveConnected = !!(config.google.clientId && config.google.refreshToken);

  // Check for session cookie files
  const { existsSync } = await import('node:fs');
  const { resolve } = await import('node:path');
  const sessionsDir = resolve('data/sessions');
  const sessions: Record<string, Date | null> = {
    instagram: null,
    tiktok: null,
    twitter: null,
  };
  for (const platform of ['instagram', 'tiktok', 'twitter'] as const) {
    const path = resolve(sessionsDir, `${platform}.json`);
    if (existsSync(path)) {
      const { statSync } = await import('node:fs');
      sessions[platform] = statSync(path).mtime;
    }
  }

  // Read current .env values for the form
  const env = readEnvFile();

  res.render('settings', {
    page: 'settings',
    gdriveConnected,
    sessions,
    env,
  });
});

// Google Drive OAuth flow
pagesRouter.get('/auth/google', (_req, res) => {
  if (!config.google.clientId || !config.google.clientSecret) {
    return res.send('<h1>Erreur</h1><p>GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET doivent etre configures dans .env</p><a href="/settings">Retour</a>');
  }
  const authUrl = getGDriveAuthUrl();
  res.redirect(authUrl);
});

pagesRouter.get('/auth/google/callback', async (req, res) => {
  const code = req.query.code as string;
  if (!code) {
    return res.send('<h1>Erreur</h1><p>Pas de code d\'autorisation recu.</p><a href="/settings">Retour</a>');
  }

  try {
    const refreshToken = await exchangeCode(code);
    res.send(`
      <h1>Google Drive connecte !</h1>
      <p>Ajoutez ce refresh token dans votre fichier <code>.env</code> :</p>
      <pre>GOOGLE_REFRESH_TOKEN=${refreshToken}</pre>
      <p>Puis relancez l'application.</p>
      <a href="/settings">Retour aux settings</a>
    `);
  } catch (err) {
    res.send(`<h1>Erreur</h1><p>${err}</p><a href="/settings">Retour</a>`);
  }
});
