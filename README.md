# AI Agent Travel Guide — Backend API

An autonomous multi-agent travel planning and storytelling backend powered by **FastAPI**, **LangGraph**, and **Google Gemini**.

## Architecture & Agents

When a request arrives, a multi-agent graph coordinates the workflow:
- **Intake Agent**: Extracts destination, travel style, duration, and interests via conversation.
- **Researcher Agent**: Geocodes destinations via Nominatim, fetches rich historical and cultural data via Wikipedia API & OpenStreetMap.
- **Curator Agent**: Ranks, filters, and selects top attractions and experiences with realistic time budgets.
- **Storyteller Agent**: Writes rich, sensory narratives and audio stories for destinations.
- **Audio & PDF Services**: Generates MP3 audio narrations via `gTTS` and downloadable custom itineraries via `reportlab`.
- **Navigator Agent**: Computes road routes via OSRM, transit options, and airport/station hubs.

---

## Quick Start

### 1. Clone & Set Up Environment

```bash
git clone https://github.com/Aniket-Vadar/AI-Agent-Travel-Guide-Backend.git
cd AI-Agent-Travel-Guide-Backend

# Copy sample configuration
cp .env.example .env
```

Open `.env` and configure your **Google Gemini API Key**:
```env
GOOGLE_API_KEY=your_gemini_api_key_here
```

### 2. Local Setup (Python 3.12+)

```bash
# Create and activate virtual environment
python -m venv .venv

# Windows:
.\.venv\Scripts\activate

# macOS / Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or run via automated dev scripts:
- Windows: `.\scripts\dev.ps1`
- Linux/macOS: `./scripts/dev.sh`

The API and interactive Swagger docs will be live at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/health/live](http://localhost:8000/health/live)

---

## Deploy to Render

### Option 1: Render Blueprint (Easiest & Automatic)
1. In [Render Dashboard](https://dashboard.render.com), click **New +** -> **Blueprint**.
2. Connect `https://github.com/Aniket-Vadar/AI-Agent-Travel-Guide-Backend`.
3. Render reads `render.yaml` automatically and configures the service, health check, and ports.
4. When prompted, enter your `GOOGLE_API_KEY`.
5. Click **Apply**.

### Option 2: Web Service Manual Setup
If you don't see the repository in Render's list:
- **Direct Git URL**: Under "Connect a repository", paste the public Git URL `https://github.com/Aniket-Vadar/AI-Agent-Travel-Guide-Backend.git` into the **"Public Git repository"** box and click **Continue**.
- **Or Grant GitHub App Permission**: Click **Configure GitHub App** (or GitHub Settings > Applications > Render > Repository Access) and select `AI-Agent-Travel-Guide-Backend`.

Settings:
- **Runtime**: Python 3
- **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`
- **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2 --proxy-headers`
- **Health Check Path**: `/health/live`
- **Environment Variables**: Add `GOOGLE_API_KEY` (and `DATABASE_URL` if not using default SQLite).

---

## Running with Docker

```bash
docker compose up --build
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `GOOGLE_API_KEY` | **Required.** Google Gemini API key | — |
| `GEMINI_MODEL` | Fast model for extraction & chat | `gemini-2.5-flash` |
| `GEMINI_PRO_MODEL` | Model for deep stories & guides | `gemini-2.5-pro` |
| `DATABASE_URL` | SQLAlchemy async connection string | `sqlite+aiosqlite:///./data/travel.db` |
| `CORS_ORIGINS` | Allowed origins (e.g., your Next.js UI URL) | `http://localhost:3000,...` |
| `LANGSMITH_API_KEY` | Optional LangSmith tracing key | — |
| `API_KEY` | Optional API key protecting `/api/v1/*` routes | unset (open) |

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```
