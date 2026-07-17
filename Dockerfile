FROM python:3.14-slim

# Install uv for fast dependency management.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (layer caching).
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source.
COPY *.py ./

# Token directory — mount as a volume so tokens survive restarts.
RUN mkdir -p /root/.garmin_tokens
VOLUME /root/.garmin_tokens

# Default to discord bot; override with `docker run ... uv run python main.py`
# for CLI mode.
CMD ["uv", "run", "python", "discord_bot.py"]
