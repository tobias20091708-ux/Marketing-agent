-- ============================================
-- ALARMS — migration for the wake-up alarm feature
-- Run manually against the Railway PostgreSQL database if you want to apply
-- it ahead of time. The app also creates this table automatically on
-- startup (idempotent CREATE TABLE IF NOT EXISTS in app/main.py), since
-- Railway's managed Postgres does not auto-run init.sql-style scripts.
-- ============================================

CREATE TABLE IF NOT EXISTS alarms (
    id SERIAL PRIMARY KEY,
    time TIME NOT NULL,
    label TEXT,
    repeat VARCHAR(20) DEFAULT 'daily',
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alarms_active ON alarms(active);
