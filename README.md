# Payment Processing Microservice

This microservice handles payment initiation, verification, and status updates for a Rental Management System, using FastAPI, PostgreSQL, and Chapa.co's sandbox API.

## Table of Contents
- [Setup](#setup)
- [Environment Variables](#environment-variables)
- [Database Setup](#database-setup)
- [Running the Application](#running-the-application)
- [API Endpoints](#api-endpoints)
- [Chapa Sandbox Setup](#chapa-sandbox-setup)
- [Testing](#testing)

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd Rent-management--system -payment-processing-service
    ```

2.  **Create a virtual environment and install dependencies:**
    ```bash
    python3.10 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

## Environment Variables

Create a `.env` file in the root directory based on `.env.example`:

```ini
CHAPA_API_KEY="your_chapa_api_key_here"
CHAPA_SECRET_KEY="your_chapa_secret_key_here"
JWT_SECRET="your_jwt_secret_key_here"
USER_MANAGEMENT_URL="http://user-management:8000/api/v1"
DATABASE_URL="postgresql+asyncpg://user:password@host:port/database"
NOTIFICATION_SERVICE_URL="http://notification-service:8000/api/v1"
PROPERTY_LISTING_SERVICE_URL="http://property-listing-service:8000/api/v1"
ENCRYPTION_KEY="a_32_byte_secret_key_for_aes_encryption" # Must be 32 bytes for AES-256
```

## Database Setup

Ensure you have a PostgreSQL database running. Update the `DATABASE_URL` in your `.env` file.

Run the migration script to create the `Payments` table:

```bash
./migrate.sh
```

## Running the Application

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API Endpoints

(Documentation will be generated here)

## Chapa Sandbox Setup

1.  Sign up for a Chapa.co sandbox account.
2.  Obtain your `CHAPA_API_KEY` and `CHAPA_SECRET_KEY` from your sandbox dashboard.
3.  Configure a webhook URL in your Chapa dashboard to point to `YOUR_SERVICE_URL/api/v1/webhook/chapa`.

## Testing

```bash
pytest
```
