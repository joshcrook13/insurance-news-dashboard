# Insurance News Dashboard

A live dashboard that scrapes **Insurance Journal**, **Business Insurance**, and **Insurance News Net**, scores articles by trending signals, and surfaces the top 10 most popular stories.

```
insurance-dashboard/
├── backend/
│   ├── main.py           ← FastAPI scraper + scoring engine
│   ├── requirements.txt
│   └── Procfile          ← Render start command
└── frontend/
    └── index.html        ← Single-file dashboard (no frameworks)
```

---

## 1 · Deploy the Backend to Render.com (free tier)

### Prerequisites
- Free account at [render.com](https://render.com)
- Your code in a GitHub or GitLab repository

### Steps

1. **Push the `backend/` folder to GitHub**
   ```
   git init && git add . && git commit -m "init"
   gh repo create insurance-news-api --public --source=. --push
   ```
   *(or push manually via the GitHub UI)*

2. **Create a new Web Service on Render**
   - Go to [dashboard.render.com](https://dashboard.render.com) → **New** → **Web Service**
   - Connect your GitHub repo
   - Set the **Root Directory** to `backend`

3. **Configure the service**
   | Setting | Value |
   |---|---|
   | Environment | `Python 3` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
   | Instance Type | **Free** |

   > Render auto-detects the `Procfile`, so the Start Command is filled in automatically.

4. **Deploy**
   - Click **Create Web Service**
   - Wait ~2 minutes for the first deploy to complete
   - Your API will be live at a URL like:
     ```
     https://insurance-news-api.onrender.com
     ```
   - Test it: `https://your-service.onrender.com/health` → should return `{"status":"ok"}`
   - Full endpoint: `https://your-service.onrender.com/news`

> **Note:** Free Render services spin down after 15 minutes of inactivity. The first request after sleep takes ~30 s to wake up. Upgrade to a paid plan ($7/mo) to avoid cold starts.

---

## 2 · Deploy the Frontend to Netlify (drag and drop)

1. Go to [app.netlify.com](https://app.netlify.com) and sign in (free account)
2. Click **Add new site** → **Deploy manually**
3. Drag the **entire `frontend/` folder** onto the drag-and-drop zone
4. Netlify will give you a URL like `https://shimmering-fox-abc123.netlify.app`

That's it — no build step needed.

---

## 3 · Update the Frontend with Your Live Render API URL

After deploying the backend, you need to tell the frontend where to call.

1. Open `frontend/index.html` in a text editor
2. Find this line near the bottom of the `<script>` block:
   ```js
   const API_BASE = "http://localhost:8000";
   ```
3. Replace it with your Render URL (no trailing slash):
   ```js
   const API_BASE = "https://your-service-name.onrender.com";
   ```
4. Save the file
5. Re-upload to Netlify:
   - Go back to your Netlify site dashboard
   - Click **Deploys** → drag the updated `frontend/` folder onto the deploy zone again

The dashboard will now call the live API.

---

## Local Development

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend — just open in a browser (API_BASE already points to localhost:8000)
open frontend/index.html
```

---

## How the Scoring Works

Each article is assigned a **relevance score** based on:

| Signal | Points |
|---|---|
| Appears on 2 sources | +10 |
| Appears on 3 sources | +15 |
| Marked trending / featured on source page | +3 per source |
| Comment count (normalised 0–4) | up to +4 |
| Prominent page position | up to +2 |

Articles covering the same story across multiple sources are **merged** into one card, making cross-source stories rise to the top naturally.
