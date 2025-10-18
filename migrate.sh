#!/bin/bash

set -e

# Load environment variables from .env file
if [ -f .env ]; then
    export $(cat .env | grep -v '#' | awk '/=/ {print $1}')
fi

DB_URL=${DATABASE_URL}

if [ -z "$DB_URL" ]; then
    echo "Error: DATABASE_URL is not set in .env file."
    exit 1
fi

# Extract connection details for psql
DB_HOST=$(echo $DB_URL | sed -e 's/.*@\([^:]*\):.*/\1/')
DB_PORT=$(echo $DB_URL | sed -e 's/.*:\([0-9]*\)\/.*$/\1/')
DB_NAME=$(echo $DB_URL | sed -e 's/.*\/\([^?]*\).*/\1/')
DB_USER=$(echo $DB_URL | sed -e 's/.*\/\/\([^:]*\):.*/\1/')
DB_PASSWORD=$(echo $DB_URL | sed -e 's/.*:\([^@]*\)@.*/\1/')

# Use PGPASSWORD for non-interactive password input
export PGPASSWORD=$DB_PASSWORD

echo "Applying schema.sql..."
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f sql/schema.sql

echo "Applying seed.sql (if exists)..."
if [ -f sql/seed.sql ]; then
    psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f sql/seed.sql
else
    echo "sql/seed.sql not found, skipping."
fi

echo "Database migration complete."
