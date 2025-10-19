#!/bin/bash
set -e

# Load environment variables safely
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

if [ -z "$DATABASE_URL" ]; then
    echo "Error: DATABASE_URL is not set in .env file."
    exit 1
fi

# Parse DATABASE_URL using regex
DB_URL_REGEX="postgresql\+asyncpg://([^:]+):([^@]+)@([^:]+):([0-9]+)/(.+)"
if [[ $DATABASE_URL =~ $DB_URL_REGEX ]]; then
    DB_USER="${BASH_REMATCH[1]}"
    DB_PASSWORD="${BASH_REMATCH[2]}"
    DB_HOST="${BASH_REMATCH[3]}"
    DB_PORT="${BASH_REMATCH[4]}"
    DB_NAME="${BASH_REMATCH[5]}"
else
    echo "Error: DATABASE_URL format is invalid."
    exit 1
fi

export PGPASSWORD=$DB_PASSWORD

echo "Applying schema.sql..."
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f sql/schema.sql

if [ -f sql/seed.sql ]; then
    echo "Applying seed.sql..."
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f sql/seed.sql
else
    echo "sql/seed.sql not found, skipping."
fi

echo "Database migration complete."
