# Samouraïs Meme Backend

Backend FFmpeg pour le traitement des vidéos meme.

## Déploiement sur Railway

### 1. Créer le projet

```bash
# Connecte-toi à Railway CLI (si pas déjà fait)
npm install -g @railway/cli
railway login

# Créer un nouveau projet
railway init
```

### 2. Déployer

```bash
# Depuis le dossier samourais-backend
railway up
```

### 3. Récupérer l'URL

Après déploiement, Railway te donnera une URL du type :
```
https://samourais-backend-production-xxxx.up.railway.app
```

### 4. Configurer le frontend

Dans le frontend, quand tu cliques sur "Exporter la vidéo", entre cette URL.
Elle sera sauvegardée dans le localStorage.

---

## API

### Health Check
```
GET /health
```

### Process Video
```
POST /api/process-video
Content-Type: multipart/form-data

- video: [fichier vidéo]
- params: JSON string avec les paramètres
```

**Paramètres disponibles :**
```json
{
  "templateWidth": 1080,
  "templateHeight": 1080,
  "frameX": 54,
  "frameY": 195,
  "frameWidth": 972,
  "frameHeight": 810,
  "frameRadius": 27,
  "trimStart": 0,
  "trimEnd": 10,
  "imageScale": 100,
  "imageOffsetX": 0,
  "imageOffsetY": 0,
  "text": "Ton texte de meme",
  "textSize": 42,
  "textX": 54,
  "textY": 40,
  "overlayText": "",
  "watermarkX": 1010,
  "watermarkY": 1040
}
```

---

## Développement local

```bash
# Install dependencies
npm install

# Run server (requires FFmpeg installed locally)
npm run dev
```

Le serveur tourne sur `http://localhost:3000`.

---

## Notes

- Max file size: 100MB
- FFmpeg est inclus dans le Docker container
- Les fichiers temporaires sont nettoyés après chaque requête
# Amourais-app
