import { Router } from 'express';
import { db } from '../../db/index.js';
import { profiles, mediaItems, scrapeJobs, type Platform } from '../../db/schema.js';
import { eq, desc, sql, count } from 'drizzle-orm';
import { createChildLogger } from '../../utils/logger.js';
import { enqueueManualScrape } from '../../scheduler/index.js';
import { readEnvFile, writeEnvFile } from '../../utils/env-editor.js';
import { writeFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import multer from 'multer';

const log = createChildLogger('api');

// Multer for cookie file uploads
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 5 * 1024 * 1024 } });

export const apiRouter = Router();

const PLATFORM_URLS: Record<Platform, (username: string) => string> = {
  instagram: (u) => `https://www.instagram.com/${u}/`,
  tiktok: (u) => `https://www.tiktok.com/@${u}`,
  twitter: (u) => `https://x.com/${u}/media`,
};

// --- Profiles ---

apiRouter.post('/profiles', async (req, res) => {
  try {
    const { platform, username } = req.body;
    const cleanUsername = username.replace(/^@/, '').trim();

    if (!cleanUsername || !['instagram', 'tiktok', 'twitter'].includes(platform)) {
      return res.status(400).json({ error: 'Platform et username requis' });
    }

    const profileUrl = PLATFORM_URLS[platform as Platform](cleanUsername);

    await db.insert(profiles).values({
      platform,
      username: cleanUsername,
      profileUrl,
    });

    log.info({ platform, username: cleanUsername }, 'Profile added');

    // Return updated profile list as HTML for htmx
    const allProfiles = await db.select().from(profiles).all();
    const profilesWithCounts = await Promise.all(
      allProfiles.map(async (profile) => {
        const [mediaCount] = await db.select({ count: count() }).from(mediaItems)
          .where(eq(mediaItems.profileId, profile.id));
        return { ...profile, mediaCount: mediaCount.count };
      }),
    );

    return res.json({ success: true, profiles: profilesWithCounts });
  } catch (err: any) {
    if (err.message?.includes('UNIQUE constraint')) {
      return res.status(409).json({ error: 'Ce profil existe deja' });
    }
    log.error({ err }, 'Failed to add profile');
    return res.status(500).json({ error: 'Erreur serveur' });
  }
});

apiRouter.patch('/profiles/:id', async (req, res) => {
  try {
    const id = parseInt(req.params.id, 10);
    const updates: Record<string, unknown> = {};

    if (req.body.isActive !== undefined) {
      updates.isActive = req.body.isActive === 'true' || req.body.isActive === true;
    }
    if (req.body.scrapeIntervalMinutes !== undefined) {
      updates.scrapeIntervalMinutes = parseInt(req.body.scrapeIntervalMinutes, 10);
    }
    if (req.body.scrapeMode !== undefined && ['backfill', 'daily'].includes(req.body.scrapeMode)) {
      updates.scrapeMode = req.body.scrapeMode;
    }

    updates.updatedAt = new Date();

    await db.update(profiles).set(updates).where(eq(profiles.id, id));
    log.info({ id, updates }, 'Profile updated');

    return res.json({ success: true });
  } catch (err) {
    log.error({ err }, 'Failed to update profile');
    return res.status(500).json({ error: 'Erreur serveur' });
  }
});

apiRouter.delete('/profiles/:id', async (req, res) => {
  try {
    const id = parseInt(req.params.id, 10);
    await db.delete(profiles).where(eq(profiles.id, id));
    log.info({ id }, 'Profile deleted');
    return res.json({ success: true });
  } catch (err) {
    log.error({ err }, 'Failed to delete profile');
    return res.status(500).json({ error: 'Erreur serveur' });
  }
});

apiRouter.post('/profiles/:id/scrape', async (req, res) => {
  try {
    const id = parseInt(req.params.id, 10);
    const [profile] = await db.select().from(profiles).where(eq(profiles.id, id));

    if (!profile) {
      return res.status(404).json({ error: 'Profil non trouve' });
    }

    // Create a new job
    const [job] = await db.insert(scrapeJobs).values({
      profileId: id,
      triggeredBy: 'manual',
    }).returning();

    log.info({ profileId: id, jobId: job.id }, 'Manual scrape triggered');

    // Queue the job in the scheduler
    enqueueManualScrape(id, job.id);

    return res.json({ success: true, jobId: job.id });
  } catch (err) {
    log.error({ err }, 'Failed to trigger scrape');
    return res.status(500).json({ error: 'Erreur serveur' });
  }
});

// --- Jobs ---

apiRouter.get('/jobs/recent', async (_req, res) => {
  const recentJobs = await db.select({
    job: scrapeJobs,
    profile: profiles,
  })
    .from(scrapeJobs)
    .leftJoin(profiles, eq(scrapeJobs.profileId, profiles.id))
    .orderBy(desc(scrapeJobs.createdAt))
    .limit(10);

  const html = recentJobs.length
    ? `<table>
        <thead><tr><th>Profil</th><th>Status</th><th>Nouveau</th><th>Upload</th><th>Date</th></tr></thead>
        <tbody>
        ${recentJobs.map(({ job, profile }) => `
          <tr>
            <td>${profile?.username || 'N/A'}</td>
            <td><span class="status-${job.status === 'completed' ? 'green' : job.status === 'running' ? 'blue' : job.status === 'failed' ? 'red' : 'orange'}">${job.status}</span></td>
            <td>${job.mediaNew}</td>
            <td>${job.mediaUploaded}</td>
            <td>${job.createdAt ? new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'short' }).format(job.createdAt) : ''}</td>
          </tr>
        `).join('')}
        </tbody></table>`
    : '<p>Aucun job pour le moment.</p>';

  res.send(html);
});

apiRouter.get('/jobs/list', async (_req, res) => {
  const allJobs = await db.select({
    job: scrapeJobs,
    profile: profiles,
  })
    .from(scrapeJobs)
    .leftJoin(profiles, eq(scrapeJobs.profileId, profiles.id))
    .orderBy(desc(scrapeJobs.createdAt))
    .limit(50);

  const html = allJobs.length
    ? allJobs.map(({ job, profile }) => `
        <tr>
          <td>${job.id}</td>
          <td>${profile?.username || 'N/A'}</td>
          <td><span class="status-${job.status === 'completed' ? 'green' : job.status === 'running' ? 'blue' : job.status === 'failed' ? 'red' : 'orange'}">${job.status}</span></td>
          <td>${job.triggeredBy}</td>
          <td>${job.mediaFound}</td>
          <td>${job.mediaNew}</td>
          <td>${job.mediaDownloaded}</td>
          <td>${job.mediaUploaded}</td>
          <td>${job.createdAt ? new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'short' }).format(job.createdAt) : ''}</td>
          <td>
            ${job.status === 'failed' ? `<button class="outline small" hx-post="/api/jobs/${job.id}/retry" hx-swap="none">Retry</button>` : ''}
          </td>
        </tr>
      `).join('')
    : '<tr><td colspan="10">Aucun job.</td></tr>';

  res.send(html);
});

apiRouter.post('/jobs/:id/retry', async (req, res) => {
  try {
    const id = parseInt(req.params.id, 10);
    const [job] = await db.select().from(scrapeJobs).where(eq(scrapeJobs.id, id));
    if (!job) return res.status(404).json({ error: 'Job non trouve' });

    // Create a new job for the same profile
    const [newJob] = await db.insert(scrapeJobs).values({
      profileId: job.profileId,
      triggeredBy: 'manual',
    }).returning();

    log.info({ oldJobId: id, newJobId: newJob.id }, 'Job retry triggered');
    return res.json({ success: true, jobId: newJob.id });
  } catch (err) {
    log.error({ err }, 'Failed to retry job');
    return res.status(500).json({ error: 'Erreur serveur' });
  }
});

// --- Status ---

apiRouter.get('/status', async (_req, res) => {
  const [profileCount] = await db.select({ count: count() }).from(profiles);
  const [mediaCount] = await db.select({ count: count() }).from(mediaItems);
  const [pendingCount] = await db.select({ count: count() }).from(mediaItems).where(eq(mediaItems.status, 'pending'));

  return res.json({
    profiles: profileCount.count,
    media: mediaCount.count,
    pending: pendingCount.count,
  });
});

// --- Settings ---

apiRouter.post('/settings/env', async (req, res) => {
  try {
    const updates: Record<string, string> = {};

    for (const [key, value] of Object.entries(req.body)) {
      if (typeof value === 'string') {
        updates[key] = value;
      }
    }

    if (Object.keys(updates).length === 0) {
      return res.status(400).send('<small style="color:red;">Aucun champ a sauvegarder</small>');
    }

    writeEnvFile(updates);
    log.info({ keys: Object.keys(updates) }, 'Settings saved to .env');

    return res.send('<small style="color:green;">Sauvegarde OK</small>');
  } catch (err) {
    log.error({ err }, 'Failed to save settings');
    return res.status(500).send('<small style="color:red;">Erreur de sauvegarde</small>');
  }
});

apiRouter.post('/settings/session', upload.single('cookies'), async (req, res) => {
  try {
    const platform = req.body?.platform;
    if (!platform || !['instagram', 'tiktok', 'twitter'].includes(platform)) {
      return res.status(400).send('<small style="color:red;">Plateforme invalide</small>');
    }

    const file = (req as any).file;
    if (!file) {
      return res.status(400).send('<small style="color:red;">Aucun fichier selectionne</small>');
    }

    const sessionsDir = resolve('data/sessions');
    mkdirSync(sessionsDir, { recursive: true });

    const content = file.buffer.toString('utf-8');

    // Validate it's valid JSON
    try {
      JSON.parse(content);
    } catch {
      return res.status(400).send('<small style="color:red;">Fichier JSON invalide</small>');
    }

    const filePath = resolve(sessionsDir, `${platform}.json`);
    writeFileSync(filePath, content, 'utf-8');

    log.info({ platform, size: file.size }, 'Session cookies uploaded');

    const now = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date());
    return res.send(`<small style="color:green;">Cookies OK (${now})</small>`);
  } catch (err) {
    log.error({ err }, 'Failed to upload session cookies');
    return res.status(500).send('<small style="color:red;">Erreur upload</small>');
  }
});
