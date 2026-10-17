FROM python:3.10-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PYTHON_DOWNLOADS=0
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    libmagic1 \
    libjpeg-dev \
    zlib1g-dev \
    postgresql-client \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from the committed lockfile.
COPY pyproject.toml uv.lock /app/
RUN uv sync --locked --no-dev --no-install-project --no-editable

# Copy project
COPY . /app/

# Create directories
RUN mkdir -p /var/log/django /var/log/supervisor /app/media /app/staticfiles /app/docs_html

# Build Sphinx documentation
RUN cd /app/docs && sphinx-build -b html . /app/docs_html 2>/dev/null || echo "Docs build skipped"

# Collect static files
RUN SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
    POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
    python manage.py collectstatic --noinput

# Copy and set permissions for entrypoint
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Copy supervisor config
COPY supervisord.conf /app/

EXPOSE 8018 18106

ENTRYPOINT ["/app/docker-entrypoint.sh"]
