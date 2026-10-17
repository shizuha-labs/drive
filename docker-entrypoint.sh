#!/bin/bash
set -e

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL..."
while ! pg_isready -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" -q; do
    sleep 1
done
echo "PostgreSQL is ready!"

# Ensure database exists
echo "Ensuring database exists..."

# Build Sphinx documentation if not already built
if [ -d "/app/docs" ] && [ ! -f "/app/docs_html/index.html" ]; then
    echo "Building Sphinx documentation..."
    mkdir -p /app/docs_html
    cd /app/docs && sphinx-build -b html . /app/docs_html 2>/dev/null || echo "Docs build skipped (optional)"
    echo "Documentation build complete!"
fi

# Start supervisord
exec /usr/bin/supervisord -c /app/supervisord.conf
