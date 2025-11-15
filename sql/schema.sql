-- sql/schema.sql

-- ensure pgcrypto is available if you use gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- if you prefer uuid-ossp, you can use:
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- create enum type only if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_status') THEN
        CREATE TYPE payment_status AS ENUM ('PENDING', 'SUCCESS', 'FAILED');
    END IF;
END$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID UNIQUE NOT NULL, -- Added for idempotency
    property_id UUID NOT NULL,
    user_id UUID NOT NULL,
    amount DECIMAL(10, 2) NOT NULL DEFAULT 500.00,
    status payment_status NOT NULL DEFAULT 'PENDING',
    chapa_tx_ref TEXT NOT NULL,
    failure_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments (user_id);
CREATE INDEX IF NOT EXISTS idx_payments_property_id ON payments (property_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments (status);
CREATE INDEX IF NOT EXISTS idx_payments_chapa_tx_ref ON payments (chapa_tx_ref);
