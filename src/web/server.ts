import express from 'express';
import { engine } from 'express-handlebars';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { config } from '../config/index.js';
import { createChildLogger } from '../utils/logger.js';
import { pagesRouter } from './routes/pages.js';
import { apiRouter } from './routes/api.js';

const log = createChildLogger('web');
const __dirname = dirname(fileURLToPath(import.meta.url));

export function createServer() {
  const app = express();

  app.engine('hbs', engine({
    extname: '.hbs',
    defaultLayout: 'layout',
    layoutsDir: join(__dirname, 'views'),
    partialsDir: join(__dirname, 'views/partials'),
    helpers: {
      eq: (a: unknown, b: unknown) => a === b,
      formatDate: (date: Date | null) => {
        if (!date) return 'Jamais';
        return new Intl.DateTimeFormat('fr-FR', {
          dateStyle: 'short',
          timeStyle: 'short',
        }).format(date instanceof Date ? date : new Date(date));
      },
      platformIcon: (platform: string) => {
        const icons: Record<string, string> = {
          instagram: '📸',
          tiktok: '🎵',
          twitter: '🐦',
        };
        return icons[platform] || '🌐';
      },
      statusColor: (status: string) => {
        const colors: Record<string, string> = {
          completed: 'green',
          running: 'blue',
          queued: 'orange',
          failed: 'red',
          uploaded: 'green',
          pending: 'gray',
          downloading: 'blue',
          uploading: 'blue',
        };
        return colors[status] || 'gray';
      },
      json: (obj: unknown) => JSON.stringify(obj),
    },
  }));
  app.set('view engine', 'hbs');
  app.set('views', join(__dirname, 'views'));

  app.use(express.json());
  app.use(express.urlencoded({ extended: true }));
  app.use(express.static(join(__dirname, 'public')));

  app.use('/', pagesRouter);
  app.use('/api', apiRouter);

  return app;
}

export function startServer() {
  const app = createServer();

  app.listen(config.port, () => {
    log.info({ port: config.port }, `SAMOURAIS SCRAPPER running at http://localhost:${config.port}`);
  });

  return app;
}
