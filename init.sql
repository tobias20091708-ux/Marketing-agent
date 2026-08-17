-- AI Operations Platform - Database Schema
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- === CORE TABLES ===

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user',
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE agents (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    type VARCHAR(50) NOT NULL,
    status VARCHAR(20) DEFAULT 'idle',
    config JSONB DEFAULT '{}',
    last_run TIMESTAMPTZ,
    stats JSONB DEFAULT '{"tasks_completed": 0, "errors": 0}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(50) REFERENCES agents(id),
    type VARCHAR(100) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    priority INTEGER DEFAULT 5,
    payload JSONB NOT NULL DEFAULT '{}',
    result JSONB,
    error TEXT,
    parent_task_id INTEGER REFERENCES tasks(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE memory (
    id SERIAL PRIMARY KEY,
    namespace VARCHAR(100) NOT NULL,
    key VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(namespace, key)
);

CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(50),
    action VARCHAR(200) NOT NULL,
    details JSONB DEFAULT '{}',
    user_approved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- === EMAIL TABLES ===

CREATE TABLE emails (
    id SERIAL PRIMARY KEY,
    message_id VARCHAR(500) UNIQUE,
    thread_id VARCHAR(500),
    from_address VARCHAR(255),
    to_addresses TEXT[],
    subject VARCHAR(1000),
    body_preview TEXT,
    full_body TEXT,
    labels TEXT[],
    category VARCHAR(50),
    priority VARCHAR(20),
    sentiment FLOAT,
    requires_action BOOLEAN DEFAULT FALSE,
    suggested_reply TEXT,
    reply_confidence FLOAT,
    status VARCHAR(50) DEFAULT 'unprocessed',
    received_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- === FINANCE TABLES ===

CREATE TABLE transactions (
    id SERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    external_id VARCHAR(255),
    type VARCHAR(50),
    amount DECIMAL(15,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'DKK',
    description TEXT,
    category VARCHAR(100),
    counterparty VARCHAR(255),
    reconciled BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    transaction_date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE financial_reports (
    id SERIAL PRIMARY KEY,
    type VARCHAR(50) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    data JSONB NOT NULL,
    generated_by VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- === CONTACTS / CRM ===

CREATE TABLE contacts (
    id SERIAL PRIMARY KEY,
    external_id VARCHAR(255),
    source VARCHAR(50),
    email VARCHAR(255),
    name VARCHAR(255),
    company VARCHAR(255),
    title VARCHAR(255),
    phone VARCHAR(50),
    lead_score INTEGER DEFAULT 0,
    stage VARCHAR(50) DEFAULT 'new',
    tags TEXT[],
    notes TEXT,
    last_interaction TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- === SUPPORT TICKETS ===

CREATE TABLE tickets (
    id SERIAL PRIMARY KEY,
    external_id VARCHAR(255),
    source VARCHAR(50),
    customer_email VARCHAR(255),
    subject VARCHAR(500),
    body TEXT,
    category VARCHAR(100),
    priority VARCHAR(20),
    status VARCHAR(50) DEFAULT 'open',
    assigned_to VARCHAR(100),
    resolution TEXT,
    auto_resolved BOOLEAN DEFAULT FALSE,
    resolution_confidence FLOAT,
    response_time_seconds INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

-- === MARKETING ===

CREATE TABLE campaigns (
    id SERIAL PRIMARY KEY,
    platform VARCHAR(50),
    external_id VARCHAR(255),
    name VARCHAR(255),
    status VARCHAR(50),
    budget DECIMAL(10,2),
    spend DECIMAL(10,2) DEFAULT 0,
    impressions BIGINT DEFAULT 0,
    clicks BIGINT DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    roas FLOAT,
    recommendations TEXT,
    data JSONB DEFAULT '{}',
    last_synced TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- === SCHEDULES ===

CREATE TABLE schedules (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(50) REFERENCES agents(id),
    name VARCHAR(255) NOT NULL,
    cron_expression VARCHAR(100) NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    task_payload JSONB DEFAULT '{}',
    enabled BOOLEAN DEFAULT TRUE,
    last_run TIMESTAMPTZ,
    next_run TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- === INDEXES ===

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_agent ON tasks(agent_id);
CREATE INDEX idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX idx_emails_status ON emails(status);
CREATE INDEX idx_emails_category ON emails(category);
CREATE INDEX idx_emails_received ON emails(received_at DESC);
CREATE INDEX idx_transactions_date ON transactions(transaction_date);
CREATE INDEX idx_contacts_email ON contacts(email);
CREATE INDEX idx_contacts_score ON contacts(lead_score DESC);
CREATE INDEX idx_tickets_status ON tickets(status);
CREATE INDEX idx_audit_created ON audit_log(created_at DESC);
CREATE INDEX idx_memory_namespace ON memory(namespace);
CREATE INDEX idx_memory_embedding ON memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- === SEED AGENTS ===

INSERT INTO agents (id, name, type, config) VALUES
('email-agent', 'Email agent', 'email', '{"check_interval": 60, "auto_reply": false}'),
('finance-agent', 'Finance agent', 'finance', '{"reconciliation_schedule": "0 6 * * *"}'),
('marketing-agent', 'Marketing agent', 'marketing', '{"report_schedule": "0 8 * * 1"}'),
('sales-agent', 'Sales agent', 'sales', '{"lead_score_threshold": 70}'),
('support-agent', 'Support agent', 'support', '{"auto_resolve_l1": true}'),
('dev-agent', 'Dev agent', 'dev', '{"auto_review": true}'),
('personal-assistant', 'Personal assistant', 'personal', '{"default_fallback": true}');
