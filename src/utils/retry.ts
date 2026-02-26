import { createChildLogger } from './logger.js';

const log = createChildLogger('retry');

export async function withRetry<T>(
  fn: () => Promise<T>,
  options: { maxRetries?: number; delayMs?: number; label?: string } = {},
): Promise<T> {
  const { maxRetries = 3, delayMs = 2000, label = 'operation' } = options;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (attempt === maxRetries) {
        log.error({ err, label, attempt }, 'All retries exhausted');
        throw err;
      }
      const wait = delayMs * attempt;
      log.warn({ err, label, attempt, nextRetryMs: wait }, 'Retrying after failure');
      await new Promise((r) => setTimeout(r, wait));
    }
  }

  throw new Error('Unreachable');
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
