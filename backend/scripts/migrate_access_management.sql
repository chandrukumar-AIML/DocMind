-- backend/scripts/migrate_access_management.sql
-- Access Management System — full schema migration
-- Run once against your PostgreSQL database.
-- Safe: uses IF NOT EXISTS / DO $$ blocks throughout.

BEGIN;

-- ════════════════════════════════════════════════════
-- 1. EXTEND user_role_enum with workspace_admin
-- ════════════════════════════════════════════════════
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'workspace_admin'
          AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'user_role_enum')
    ) THEN
        ALTER TYPE user_role_enum ADD VALUE 'workspace_admin';
    END IF;
END $$;

-- ════════════════════════════════════════════════════
-- 2. ADD NEW COLUMNS TO users
-- ════════════════════════════════════════════════════
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS invited_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS last_login        TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS global_role       VARCHAR(20) DEFAULT 'viewer'
        CHECK (global_role IN ('superadmin','workspace_admin','editor','viewer'));

-- Back-fill global_role from is_superuser flag
UPDATE users SET global_role = 'superadmin' WHERE is_superuser = TRUE AND global_role = 'viewer';

-- ════════════════════════════════════════════════════
-- 3. EXTEND workspaces TABLE
-- ════════════════════════════════════════════════════
ALTER TABLE workspaces
    ADD COLUMN IF NOT EXISTS client_name         VARCHAR(128),
    ADD COLUMN IF NOT EXISTS client_email        VARCHAR(255),
    ADD COLUMN IF NOT EXISTS plan                VARCHAR(20) DEFAULT 'starter'
        CHECK (plan IN ('starter','business','enterprise')),
    ADD COLUMN IF NOT EXISTS max_docs            INTEGER DEFAULT 100,
    ADD COLUMN IF NOT EXISTS max_queries_per_day INTEGER DEFAULT 500,
    ADD COLUMN IF NOT EXISTS max_storage_gb      FLOAT   DEFAULT 5.0,
    ADD COLUMN IF NOT EXISTS storage_used_mb     FLOAT   DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS query_count_today   INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS doc_count           INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS suspended_at        TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS suspended_reason    TEXT,
    ADD COLUMN IF NOT EXISTS domain_type         VARCHAR(50);

-- ════════════════════════════════════════════════════
-- 4. INVITES TABLE
-- ════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS invites (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        VARCHAR(255) NOT NULL,
    workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    role         VARCHAR(20) NOT NULL DEFAULT 'workspace_admin'
                 CHECK (role IN ('workspace_admin','editor','viewer')),
    token_hash   VARCHAR(255) UNIQUE NOT NULL,
    token_prefix VARCHAR(12) NOT NULL,
    invited_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    expires_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    accepted_at  TIMESTAMP WITH TIME ZONE,
    resent_at    TIMESTAMP WITH TIME ZONE,
    status       VARCHAR(20) NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','accepted','expired','revoked')),
    created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_invites_email         ON invites(email);
CREATE INDEX IF NOT EXISTS ix_invites_workspace_id  ON invites(workspace_id);
CREATE INDEX IF NOT EXISTS ix_invites_status        ON invites(status);
CREATE INDEX IF NOT EXISTS ix_invites_expires_at    ON invites(expires_at);

-- ════════════════════════════════════════════════════
-- 5. API KEYS TABLE
-- ════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    key_hash      VARCHAR(255) NOT NULL UNIQUE,
    key_prefix    VARCHAR(20) NOT NULL,
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    last_used_at  TIMESTAMP WITH TIME ZONE,
    usage_count   INTEGER DEFAULT 0,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at    TIMESTAMP WITH TIME ZONE,
    scopes        TEXT[] DEFAULT ARRAY['read','write'],
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_api_keys_workspace_id ON api_keys(workspace_id);
CREATE INDEX IF NOT EXISTS ix_api_keys_prefix       ON api_keys(key_prefix);
CREATE INDEX IF NOT EXISTS ix_api_keys_active       ON api_keys(is_active, workspace_id);

-- ════════════════════════════════════════════════════
-- 6. USAGE LOGS TABLE
-- ════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS usage_logs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
    action_type      VARCHAR(50) NOT NULL,
    resource_type    VARCHAR(50),
    resource_id      UUID,
    tokens_used      INTEGER DEFAULT 0,
    ocr_pages        INTEGER DEFAULT 0,
    storage_delta_mb FLOAT   DEFAULT 0.0,
    metadata         JSONB   DEFAULT '{}',
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_usage_logs_workspace_id ON usage_logs(workspace_id);
CREATE INDEX IF NOT EXISTS ix_usage_logs_created_at   ON usage_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_usage_logs_action_type  ON usage_logs(action_type);
CREATE INDEX IF NOT EXISTS ix_usage_logs_ws_date      ON usage_logs(workspace_id, created_at);

-- ════════════════════════════════════════════════════
-- 7. AUDIT LOG TABLE
-- ════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE SET NULL,
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    action          VARCHAR(100) NOT NULL,
    resource_type   VARCHAR(50),
    resource_id     VARCHAR(255),
    ip_address      VARCHAR(45),
    user_agent      TEXT,
    request_data    JSONB DEFAULT '{}',
    response_status INTEGER,
    severity        VARCHAR(10) DEFAULT 'info'
                    CHECK (severity IN ('info','warn','error','critical')),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_audit_log_workspace_id ON audit_log(workspace_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_user_id      ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_action       ON audit_log(action);
CREATE INDEX IF NOT EXISTS ix_audit_log_created_at   ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_log_ws_date      ON audit_log(workspace_id, created_at);

-- ════════════════════════════════════════════════════
-- 8. IMPERSONATION TOKENS TABLE (superadmin only)
-- ════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS impersonation_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash      VARCHAR(255) UNIQUE NOT NULL,
    issued_by       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at         TIMESTAMP WITH TIME ZONE,
    revoked         BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_impersonation_expires ON impersonation_tokens(expires_at);

COMMIT;
