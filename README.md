# Insurance News Dashboard

A live dashboard that scrapes **Insurance Journal**, **Business Insurance**, and **Insurance News Net**, scores articles by trending signals, and surfaces the top 10 most popular stories.

---

## 0a · Anthropic API Key Setup (AI categorisation)

### Add ANTHROPIC_API_KEY to Render

1. Go to your Render dashboard → click your **insurance-news-dashboard** service
2. Click **Environment** in the left menu
3. Click **Add Environment Variable**
4. Key: `ANTHROPIC_API_KEY` · Value: your key from console.anthropic.com
5. Click **Save Changes** — Render will automatically redeploy

The key is used server-side only and never exposed to the frontend.

If the key is missing or the API call fails, the backend silently falls back to keyword-based categorisation.

---

## 0c · Admin Page Setup

### 1 · Run this SQL in Supabase SQL Editor

Go to **Supabase → SQL Editor → New query**, paste and run:

```sql
-- User profiles (auto-created for every new auth user)
create table profiles (
  id          uuid references auth.users(id) primary key,
  email       text,
  role        text    default 'user',
  status      text    default 'active',
  created_at  timestamp default now(),
  last_seen   timestamp,
  last_login  timestamp,
  articles_read integer default 0
);

create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, email, role)
  values (new.id, new.email, 'user');
  return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- Article click tracking
create table article_reads (
  id            uuid default gen_random_uuid() primary key,
  user_id       uuid references auth.users(id),
  article_url   text,
  article_title text,
  category      text,
  source        text,
  read_at       timestamp default now()
);

-- Helper function to increment articles_read counter
create or replace function increment_articles_read(uid uuid)
returns void as $$
  update profiles set articles_read = articles_read + 1 where id = uid;
$$ language sql security definer;

-- News sources configuration
create table sources (
  id        uuid default gen_random_uuid() primary key,
  name      text not null,
  url       text not null,
  enabled   boolean default true,
  added_at  timestamp default now()
);

-- Pre-populate with current sources
insert into sources (name, url) values
  ('Insurance Journal',   'https://www.insurancejournal.com/news/'),
  ('Business Insurance',  'https://www.businessinsurance.com/'),
  ('Carrier Management',  'https://www.carriermanagement.com/'),
  ('Claims Journal',      'https://www.claimsjournal.com/'),
  ('Insurance Business',  'https://www.insurancebusinessmag.com/');

-- Row Level Security
alter table profiles      enable row level security;
alter table article_reads enable row level security;
alter table sources       enable row level security;

-- Profiles: users read their own row; admins read/update all
create policy "own profile"
  on profiles for select using (auth.uid() = id);

create policy "admin read all profiles"
  on profiles for select
  using (exists (select 1 from profiles where id = auth.uid() and role = 'admin'));

create policy "admin update profiles"
  on profiles for update
  using (exists (select 1 from profiles where id = auth.uid() and role = 'admin'));

create policy "own update last_seen"
  on profiles for update using (auth.uid() = id);

-- Article reads: users insert their own; admins read all
create policy "insert own reads"
  on article_reads for insert with check (auth.uid() = user_id);

create policy "admin read all reads"
  on article_reads for select
  using (exists (select 1 from profiles where id = auth.uid() and role = 'admin'));

-- Sources: authenticated users read; admins manage
create policy "authenticated read sources"
  on sources for select using (auth.role() = 'authenticated');

create policy "admin manage sources"
  on sources for all
  using (exists (select 1 from profiles where id = auth.uid() and role = 'admin'));
```

### 2 · Make yourself admin

```sql
update profiles set role = 'admin'
where email = 'YOUR_EMAIL_HERE';
```

Replace `YOUR_EMAIL_HERE` with your actual email (e.g. `josh@crook.uk`).

### 3 · Add Render environment variables for invite functionality

In Render → your service → **Environment**, add:

| Key | Value |
|---|---|
| `SUPABASE_URL` | `https://ogpfdrpoujrekgbrfilk.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Your service role key from Supabase → Settings → API → **service_role** |

The service role key is **never exposed to the frontend** — it lives only on Render.

---

## 0b · Supabase Auth Setup (invite-only magic link)

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

### Add your hosting URL to Supabase allowed redirects

1. In Supabase → **Authentication** → **URL Configuration**
2. Set **Site URL** to `https://insurance-news-dashboard.josh-0c6.workers.dev`
3. Under **Redirect URLs** add: `https://insurance-news-dashboard.josh-0c6.workers.dev/index.html`

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

## 2 · Deploy the Frontend

Frontend is hosted on Cloudflare Workers at:
**https://insurance-news-dashboard.josh-0c6.workers.dev**

Deploy using Wrangler CLI or via the Cloudflare dashboard.

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
5. Redeploy the frontend to Cloudflare Workers.

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
