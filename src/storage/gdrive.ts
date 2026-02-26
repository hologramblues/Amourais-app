import { google } from 'googleapis';
import { createReadStream } from 'node:fs';
import { basename } from 'node:path';
import { config } from '../config/index.js';
import { createChildLogger } from '../utils/logger.js';
import type { Platform } from '../db/schema.js';

const log = createChildLogger('gdrive');

let driveClient: ReturnType<typeof google.drive> | null = null;
const folderCache = new Map<string, string>();

function getAuth() {
  const oauth2Client = new google.auth.OAuth2(
    config.google.clientId,
    config.google.clientSecret,
    config.google.redirectUri,
  );

  oauth2Client.setCredentials({
    refresh_token: config.google.refreshToken,
  });

  return oauth2Client;
}

function getDrive() {
  if (driveClient) return driveClient;

  if (!config.google.clientId || !config.google.refreshToken) {
    throw new Error('Google Drive not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN in .env');
  }

  driveClient = google.drive({ version: 'v3', auth: getAuth() });
  return driveClient;
}

export function isGDriveConfigured(): boolean {
  return !!(config.google.clientId && config.google.clientSecret && config.google.refreshToken);
}

export function getGDriveAuthUrl(): string {
  const oauth2Client = new google.auth.OAuth2(
    config.google.clientId,
    config.google.clientSecret,
    config.google.redirectUri,
  );

  return oauth2Client.generateAuthUrl({
    access_type: 'offline',
    scope: ['https://www.googleapis.com/auth/drive.file'],
    prompt: 'consent',
  });
}

export async function exchangeCode(code: string): Promise<string> {
  const oauth2Client = new google.auth.OAuth2(
    config.google.clientId,
    config.google.clientSecret,
    config.google.redirectUri,
  );

  const { tokens } = await oauth2Client.getToken(code);
  if (!tokens.refresh_token) {
    throw new Error('No refresh token received. Make sure prompt=consent is set.');
  }

  return tokens.refresh_token;
}

async function ensureFolder(parentId: string | null, name: string): Promise<string> {
  const cacheKey = `${parentId || 'root'}/${name}`;
  const cached = folderCache.get(cacheKey);
  if (cached) return cached;

  const drive = getDrive();

  // Check if folder already exists
  const query = parentId
    ? `name='${name}' and '${parentId}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false`
    : `name='${name}' and 'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false`;

  const existing = await drive.files.list({ q: query, fields: 'files(id, name)' });

  if (existing.data.files && existing.data.files.length > 0) {
    const folderId = existing.data.files[0].id!;
    folderCache.set(cacheKey, folderId);
    return folderId;
  }

  // Create the folder
  const folder = await drive.files.create({
    requestBody: {
      name,
      mimeType: 'application/vnd.google-apps.folder',
      parents: parentId ? [parentId] : undefined,
    },
    fields: 'id',
  });

  const folderId = folder.data.id!;
  folderCache.set(cacheKey, folderId);
  log.info({ name, parentId, folderId }, 'Created Google Drive folder');

  return folderId;
}

export interface UploadResult {
  fileId: string;
  webViewLink: string;
}

export async function uploadToGDrive(
  localPath: string,
  platform: Platform,
  username: string,
  postId: string,
  mimeType: string,
): Promise<UploadResult> {
  if (!isGDriveConfigured()) {
    throw new Error('Google Drive not configured');
  }

  const drive = getDrive();

  // Ensure folder structure: ROOT / Platform / @username
  const rootFolderId = await ensureFolder(null, config.gdrive.rootFolderName);

  const platformName = platform.charAt(0).toUpperCase() + platform.slice(1);
  const platformFolderId = await ensureFolder(rootFolderId, platformName);
  const userFolderId = await ensureFolder(platformFolderId, `@${username}`);

  const fileName = basename(localPath);

  log.info({ localPath, fileName, userFolderId }, 'Uploading to Google Drive');

  const response = await drive.files.create({
    requestBody: {
      name: fileName,
      parents: [userFolderId],
    },
    media: {
      mimeType,
      body: createReadStream(localPath),
    },
    fields: 'id, webViewLink',
  });

  const result: UploadResult = {
    fileId: response.data.id!,
    webViewLink: response.data.webViewLink || `https://drive.google.com/file/d/${response.data.id}/view`,
  };

  log.info({ fileId: result.fileId }, 'Upload complete');
  return result;
}
