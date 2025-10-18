-- sql/seed.sql

-- Function to generate random UUIDs for test data
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

INSERT INTO payments (id, request_id, property_id, user_id, amount, status, chapa_tx_ref, created_at, updated_at) VALUES
-- 5 PENDING payments
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'PENDING', 'chapa_ref_pending_1', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'PENDING', 'chapa_ref_pending_2', NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'PENDING', 'chapa_ref_pending_3', NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'PENDING', 'chapa_ref_pending_4', NOW() - INTERVAL '4 days', NOW() - INTERVAL '4 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'PENDING', 'chapa_ref_pending_5', NOW() - INTERVAL '5 days', NOW() - INTERVAL '5 days'),

-- 10 SUCCESS payments
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_1', NOW() - INTERVAL '10 days', NOW() - INTERVAL '10 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_2', NOW() - INTERVAL '9 days', NOW() - INTERVAL '9 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_3', NOW() - INTERVAL '8 days', NOW() - INTERVAL '8 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_4', NOW() - INTERVAL '7 days', NOW() - INTERVAL '7 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_5', NOW() - INTERVAL '6 days', NOW() - INTERVAL '6 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_6', NOW() - INTERVAL '5 days', NOW() - INTERVAL '5 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_7', NOW() - INTERVAL '4 days', NOW() - INTERVAL '4 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_8', NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_9', NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'SUCCESS', 'chapa_ref_success_10', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),

-- 5 FAILED payments
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'FAILED', 'chapa_ref_failed_1', NOW() - INTERVAL '15 days', NOW() - INTERVAL '14 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'FAILED', 'chapa_ref_failed_2', NOW() - INTERVAL '14 days', NOW() - INTERVAL '13 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'FAILED', 'chapa_ref_failed_3', NOW() - INTERVAL '13 days', NOW() - INTERVAL '12 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'FAILED', 'chapa_ref_failed_4', NOW() - INTERVAL '12 days', NOW() - INTERVAL '11 days'),
(uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), uuid_generate_v4(), 100.00, 'FAILED', 'chapa_ref_failed_5', NOW() - INTERVAL '11 days', NOW() - INTERVAL '10 days');