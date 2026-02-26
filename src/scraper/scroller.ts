import type Hero from '@ulixee/hero';
import { createChildLogger } from '../utils/logger.js';
import { sleep } from '../utils/retry.js';
import { config } from '../config/index.js';

const log = createChildLogger('scroller');

export interface ScrollOptions {
  maxScrolls?: number;
  scrollPauseMs?: number;
  shouldStop?: () => boolean;
  onScroll?: (scrollCount: number) => void;
}

export async function scrollAndCollect(hero: Hero, options: ScrollOptions = {}): Promise<void> {
  const {
    maxScrolls = config.scraper.maxScrolls,
    scrollPauseMs = config.scraper.scrollPauseMs,
    shouldStop = () => false,
    onScroll,
  } = options;

  let previousHeight = 0;
  let noChangeCount = 0;

  for (let i = 0; i < maxScrolls; i++) {
    if (shouldStop()) {
      log.info({ scrollCount: i }, 'Stopping scroll: hit known content');
      break;
    }

    // Scroll to bottom
    await hero.interact({ scroll: { y: 99999 } });
    await sleep(scrollPauseMs);

    // Check if new content loaded
    const currentHeight = await hero.activeTab.mainFrameEnvironment.execJsPath(
      'document.body.scrollHeight',
    ) as any;
    const height = typeof currentHeight === 'object' ? currentHeight.value : currentHeight;

    if (height === previousHeight) {
      noChangeCount++;
      if (noChangeCount >= 3) {
        log.info({ scrollCount: i }, 'Stopping scroll: no new content after 3 attempts');
        break;
      }
    } else {
      noChangeCount = 0;
      previousHeight = height;
    }

    onScroll?.(i + 1);
    log.debug({ scrollCount: i + 1, height }, 'Scrolled');
  }
}
