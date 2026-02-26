import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { createChildLogger } from './logger.js';

const log = createChildLogger('env-editor');

const ENV_PATH = resolve(process.cwd(), '.env');

export interface EnvValues {
  [key: string]: string;
}

/**
 * Read all key-value pairs from the .env file
 */
export function readEnvFile(): EnvValues {
  if (!existsSync(ENV_PATH)) {
    log.warn('No .env file found');
    return {};
  }

  const content = readFileSync(ENV_PATH, 'utf-8');
  const values: EnvValues = {};

  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    // Skip comments and empty lines
    if (!trimmed || trimmed.startsWith('#')) continue;

    const eqIndex = trimmed.indexOf('=');
    if (eqIndex === -1) continue;

    const key = trimmed.substring(0, eqIndex).trim();
    const value = trimmed.substring(eqIndex + 1).trim();

    values[key] = value;
  }

  return values;
}

/**
 * Update specific keys in the .env file, preserving comments and order.
 * New keys are appended at the end.
 */
export function writeEnvFile(updates: EnvValues): void {
  let content = '';

  if (existsSync(ENV_PATH)) {
    content = readFileSync(ENV_PATH, 'utf-8');
  }

  const lines = content.split('\n');
  const updatedKeys = new Set<string>();

  // Update existing lines
  const newLines = lines.map((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return line;

    const eqIndex = trimmed.indexOf('=');
    if (eqIndex === -1) return line;

    const key = trimmed.substring(0, eqIndex).trim();

    if (key in updates) {
      updatedKeys.add(key);
      return `${key}=${updates[key]}`;
    }

    return line;
  });

  // Append new keys that weren't in the file
  for (const [key, value] of Object.entries(updates)) {
    if (!updatedKeys.has(key)) {
      newLines.push(`${key}=${value}`);
    }
  }

  writeFileSync(ENV_PATH, newLines.join('\n'), 'utf-8');
  log.info({ keys: Object.keys(updates) }, '.env file updated');
}

/**
 * Mask a sensitive value for display (show first 4 and last 4 chars)
 */
export function maskValue(value: string): string {
  if (!value || value.length < 10) return value ? '••••••••' : '';
  return `${value.substring(0, 4)}••••${value.substring(value.length - 4)}`;
}
