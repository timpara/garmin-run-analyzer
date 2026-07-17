# Garmin Run Analyzer

An AI-powered running coach that connects to your Garmin Connect account and answers natural-language questions about your training, recovery, and fitness. Also runs as a Discord bot with a daily workout recommendation.

## Features

- Fetches live data from Garmin Connect (runs, HR zones, sleep, HRV, training load, body battery, race predictions, shoe mileage, and more)
- AI agent (GPT via Azure AI Foundry) that acts as an elite running coach
- **CLI mode** — interactive terminal chat
- **Discord bot mode** — chat with the coach in DMs; daily workout recommendation posted to `#daily-workout` at 07:00, 08:00, and 09:00 (configurable timezone)

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
- Every morning at **07:00, 08:00, and 09:00** the bot posts a workout recommendation to `#daily-workout`, refreshing as your sleep/HRV data syncs from your device
- If `DISCORD_ALLOWED_USER_ID` is set, the bot only responds to that user

**Finding your Discord user ID:**
Settings → Advanced → enable Developer Mode, then right-click your username → Copy User ID.

## Project Structure

```
main.py           # CLI entry point
discord_bot.py    # Discord bot entry point
agent.py          # Pydantic AI agent, system prompt, and all tools
garmin_client.py  # Garmin Connect API wrapper
models.py         # Pydantic data models
```

## Security

- `.env` is gitignored — never commit it
- Use `DISCORD_ALLOWED_USER_ID` to restrict bot responses to your account only
- Keep your bot set to "Private" in the Discord Developer Portal
