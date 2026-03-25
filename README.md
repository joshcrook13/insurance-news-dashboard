# Insurance News Dashboard

A live dashboard that scrapes **Insurance Journal**, **Business Insurance**, and **Insurance News Net**, scores articles by trending signals, and surfaces the top 10 most popular stories.

---

## 0 · Supabase Auth Setup (invite-only magic link)

### Create a free Supabase project

1. Go to [supabase.com](https://supabase.com) → **Start your project** → sign in with GitHub
2. Click **New project**, give it a name (e.g. `insurance-daily`), choose a region, set a database password → **Create new project**
3. Wait ~1 minute for the project to provision

### Find your Supabase URL and anon key

1. In your Supabase project → click **Project Settings** (gear icon, left sidebar)
2. Click **API**
3. Copy:
   - **Project URL** → this is your `SUPABASE_URL`
   - **anon / public** key → this is your `SUPABASE_ANON_KEY`

### Disable public sign-ups (invite only)

1. In Supabase → **Authentication** → **Providers** → **Email**
2. Toggle **Enable Email provider** ON
3. Toggle **Confirm email** ON
4. In Supabase → **Authentication** → **Settings**
5. Set **Enable Sign Ups** to **OFF** — this blocks anyone not invited

### Update the config in both HTML files

Open `frontend/index.html` and `frontend/login.html`. In each file find:

```js
const SUPABASE_URL      = 'YOUR_SUPABASE_URL';
const SUPABASE_ANON_KEY = 'YOUR_SUPABASE_ANON_KEY';
```

Replace the placeholder strings with your actual values. Example:

```js
const SUPABASE_URL      = 'https://abcdefgh.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...';
```

### Add your Netlify URL to Supabase allowed redirects

1. In Supabase → **Authentication** → **URL Configuration**
2. Set **Site URL** to your Netlify URL (e.g. `https://your-site.netlify.app`)
3. Under **Redirect URLs** add: `https://your-site.netlify.app/index.html`

### Invite users

Only invited users can access the dashboard — there is no self sign-up.

1. In Supabase → **Authentication** → **Users**
2. Click **Invite user**
3. Enter the user's email address → **Send invitation**
4. They receive an email with a magic link
5. Clicking it logs them in and redirects to the dashboard
6. All subsequent sign-ins use the same magic link flow from `login.html`

---

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
