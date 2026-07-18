# Garmin Run Analyzer

An AI-powered running coach that connects to your Garmin Connect account and answers natural-language questions about your training, recovery, and fitness. Also runs as a Discord bot with a daily workout recommendation and a built-in KPI dashboard.

## Features

- Fetches live data from Garmin Connect (runs, HR zones, sleep, HRV, training load, body battery, race predictions, shoe mileage, and more)
- AI agent (GPT via Azure AI Foundry) that acts as an elite running coach
- **CLI mode** — interactive terminal chat
- **Discord bot mode** — chat with the coach in DMs; daily workout recommendation posted to `#daily-workout` at 07:30 (configurable timezone)
- **KPI dashboard** — a web-based training dashboard running alongside the bot at `http://<host>:8080` showing daily wellness stats, weekly activity overview, training load by sport, and the AI workout suggestion

## Requirements

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) for dependency management
- A Garmin Connect account
- Azure AI Foundry endpoint (or any OpenAI-compatible API)
- A Discord bot token (for bot mode)

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
GARMIN_EMAIL=your@email.com
GARMIN_PASSWORD=yourpassword
AI_FOUNDRY_BASE_URL=https://your-endpoint.inference.ai.azure.com/v1
AI_FOUNDRY_API_KEY=your-api-key
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_ALLOWED_USER_ID=your-discord-user-id   # optional but recommended
DISCORD_TIMEZONE=Europe/Berlin                  # defaults to UTC
```

## Usage

### CLI mode

```bash
uv run python main.py
```

Example questions you can ask:
- Am I recovered enough to do a hard workout today?
- Assess my current fitness and training load
- How has my pace changed over the last month?
- What workout should I do next, and why?
- Build me a training week toward a sub-20 5k

### Discord bot mode

```bash
uv run python discord_bot.py
```

**Setup steps:**

1. Create a Discord application at https://discord.com/developers/applications
2. Under **Bot**: turn off "Public Bot", enable **Message Content Intent**
3. Invite the bot to a private server (it needs to share a server with you to allow DMs)
4. Create a `#daily-workout` channel in your server for the scheduled posts
5. Add your bot token and user ID to `.env`

**How it works:**

- Chat with the bot via **DMs** for a private coaching conversation
- Every morning at **07:30** the bot posts a workout recommendation to `#daily-workout`
- If `DISCORD_ALLOWED_USER_ID` is set, the bot only responds to that user
- The **KPI dashboard** starts automatically at `http://localhost:8080` (or the port set via `DASHBOARD_PORT`)

**Finding your Discord user ID:**
Settings → Advanced → enable Developer Mode, then right-click your username → Copy User ID.

## Docker / TrueNAS Deployment

The Discord bot can run as a Docker container, which makes it easy to deploy on TrueNAS SCALE or any Docker host.

### First run (interactive MFA login)

Garmin requires an MFA code on first login. Run the container interactively once to complete authentication:

```bash
docker compose run -it garmin-coach
```

Enter the MFA code when prompted. The session token is saved to a persistent volume (`garmin-tokens`) and reused on subsequent starts.

### Normal operation

After the initial login, run detached:

```bash
docker compose up -d
```

The container restarts automatically on reboot (`unless-stopped` policy).

### TrueNAS SCALE

#### Option A: Custom App (recommended)

The image is automatically built and published to GHCR on every push. Use it directly from the TrueNAS UI:

1. Go to **Apps → Discover → Custom App**
2. Fill in the form:
   - **Application Name:** `garmin-run-analyzer`
   - **Image Repository:** `ghcr.io/timpara/garmin-run-analyzer`
   - **Tag:** `latest`
   - **Container Configuration → Environment Variables:** add all variables from `.env.example` (`GARMIN_EMAIL`, `GARMIN_PASSWORD`, `AI_FOUNDRY_BASE_URL`, `AI_FOUNDRY_API_KEY`, `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USER_ID`, `DISCORD_TIMEZONE`, `DISCORD_CHANNELS`)
   - **Storage Configuration:** add a host path or volume mount at `/root/.garmin_tokens` to persist Garmin session tokens
   - **Port Forwarding:** add a port mapping: **Container Port** `8080` → **Host Port** `8080` (or any free port). This exposes the KPI dashboard at `http://<truenas-ip>:8080`
3. Deploy the app

**Initial MFA login:** The TrueNAS Custom App doesn't support interactive input. SSH into TrueNAS and run the container interactively once to complete MFA:

```bash
docker run -it --rm \
  --env-file /path/to/your/.env \
  -v garmin-tokens:/root/.garmin_tokens \
  ghcr.io/timpara/garmin-run-analyzer
```

After entering the MFA code and seeing "Discord bot connected", press Ctrl+C. The token is saved in the volume. Now start the app from the TrueNAS UI — it will reuse the saved token.

#### Option B: Docker Compose via SSH

1. Clone this repo to a dataset on your NAS (e.g., `/mnt/pool/apps/garmin-run-analyzer`)
2. Create a `.env` file with your credentials
3. SSH into TrueNAS and run `docker compose run -it garmin-coach` for the initial MFA login
4. Then `docker compose up -d` to run in the background
5. The bot survives reboots via the restart policy

### Token expiry

Garmin session tokens eventually expire. When they do, the bot will fail to fetch data. To re-authenticate:

```bash
docker compose down
docker compose run -it garmin-coach
# complete MFA, then Ctrl+C
docker compose up -d
```

## Project Structure

```
main.py            # CLI entry point
discord_bot.py     # Discord bot entry point (+ dashboard startup)
dashboard.py       # KPI dashboard (FastAPI web app)
agent.py           # Pydantic AI agent, system prompt, and all tools
garmin_client.py   # Garmin Connect API wrapper
models.py          # Pydantic data models
Dockerfile         # Container image
docker-compose.yml # Docker Compose config
```

## Security

- `.env` is gitignored — never commit it
- Use `DISCORD_ALLOWED_USER_ID` to restrict bot responses to your account only
- Keep your bot set to "Private" in the Discord Developer Portal
