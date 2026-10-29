CREATE TABLE openair_external_execution (
    dispatch_id TEXT PRIMARY KEY REFERENCES openair_dispatch(dispatch_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE RESTRICT,
    execution_decision_id TEXT NOT NULL,
    work_unit_id TEXT NOT NULL UNIQUE,
    capability_call_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    adapter TEXT NOT NULL,
    provider_idempotency_key TEXT NOT NULL,
    receipt TEXT NULL,
    remote_run_id TEXT NULL,
    external_state TEXT NOT NULL,
    failure_code TEXT NULL,
    canonical_payload_fingerprint CHAR(64) NOT NULL,
    result_evidence_refs TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (attempt_number > 0),
    CHECK (external_state IN ('never_submitted', 'submitted', 'running', 'completed', 'known_failed', 'unknown')),
    CHECK (provider_idempotency_key <> '' AND owner_id <> '' AND workspace_id <> ''),
    CHECK (canonical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (external_state NOT IN ('submitted', 'running', 'completed') OR remote_run_id IS NOT NULL),
    CHECK (external_state <> 'known_failed' OR failure_code IS NOT NULL),
    CHECK (external_state <> 'never_submitted' OR (receipt IS NULL AND remote_run_id IS NULL)),
    CHECK (updated_at >= created_at)
);

CREATE INDEX idx_openair_external_execution_task
    ON openair_external_execution(task_id, external_state, updated_at DESC, dispatch_id);
CREATE INDEX idx_openair_external_execution_scope
    ON openair_external_execution(owner_id, workspace_id, task_id, work_unit_id);

CREATE TABLE openair_external_evidence (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE RESTRICT,
    execution_decision_id TEXT NOT NULL,
    work_unit_id TEXT NOT NULL,
    capability_call_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL REFERENCES openair_dispatch(dispatch_id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    adapter TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    failure_class TEXT NOT NULL,
    normalized_finish_reason TEXT NOT NULL,
    diagnostics JSONB NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    byte_length BIGINT NOT NULL,
    truncated BOOLEAN NOT NULL,
    hash_incomplete BOOLEAN NOT NULL,
    raw_availability TEXT NOT NULL,
    encryption_algorithm TEXT NULL,
    encryption_version INTEGER NULL,
    key_id TEXT NULL,
    nonce BYTEA NULL,
    ciphertext BYTEA NULL,
    auth_tag BYTEA NULL,
    canonical_metadata_fingerprint CHAR(64) NOT NULL,
    canonical_payload_fingerprint CHAR(64) NOT NULL,
    reconciliation_evidence_refs TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    purged_at TIMESTAMPTZ NULL,
    CHECK (attempt_number > 0 AND byte_length >= 0),
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (canonical_metadata_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (canonical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (jsonb_typeof(diagnostics) = 'array'),
    CHECK (raw_availability IN ('encrypted', 'redacted_only', 'purged')),
    CHECK (
        (raw_availability = 'encrypted' AND encryption_algorithm = 'AES-256-GCM'
         AND encryption_version = 1 AND key_id IS NOT NULL AND octet_length(nonce) = 12
         AND ciphertext IS NOT NULL AND octet_length(auth_tag) = 16 AND purged_at IS NULL)
        OR
        (raw_availability IN ('redacted_only', 'purged') AND encryption_algorithm IS NULL
         AND encryption_version IS NULL AND key_id IS NULL AND nonce IS NULL
         AND ciphertext IS NULL AND auth_tag IS NULL)
    ),
    CHECK ((raw_availability = 'purged') = (purged_at IS NOT NULL)),
    CHECK (expires_at > created_at AND expires_at <= created_at + INTERVAL '30 days'),
    UNIQUE (key_id, nonce)
);

CREATE INDEX idx_openair_external_evidence_scope
    ON openair_external_evidence(owner_id, workspace_id, task_id, created_at DESC, evidence_id);
CREATE INDEX idx_openair_external_evidence_expiry
    ON openair_external_evidence(expires_at, evidence_id)
    WHERE raw_availability = 'encrypted';

CREATE TABLE openair_external_evidence_access_audit (
    access_id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL REFERENCES openair_external_evidence(evidence_id) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    purpose_code TEXT NOT NULL,
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE openair_external_reconciliation (
    command_request_id TEXT PRIMARY KEY,
    canonical_payload_fingerprint CHAR(64) NOT NULL,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE RESTRICT,
    execution_decision_id TEXT NOT NULL,
    work_unit_id TEXT NOT NULL,
    capability_call_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL REFERENCES openair_dispatch(dispatch_id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    resolution TEXT NOT NULL,
    resolved_external_state TEXT NOT NULL,
    expected_task_revision BIGINT NOT NULL,
    committed_task_revision BIGINT NOT NULL,
    error_code TEXT NULL,
    result_evidence_refs TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (canonical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (attempt_number > 0 AND expected_task_revision >= 0 AND committed_task_revision > expected_task_revision),
    CHECK (resolved_external_state IN ('never_submitted', 'submitted', 'running', 'completed', 'known_failed', 'unknown'))
);

CREATE TABLE openair_external_reconciliation_evidence (
    command_request_id TEXT NOT NULL REFERENCES openair_external_reconciliation(command_request_id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL REFERENCES openair_external_evidence(evidence_id) ON DELETE RESTRICT,
    PRIMARY KEY (command_request_id, evidence_id)
);

CREATE TABLE openair_external_retry_attempt (
    command_request_id TEXT PRIMARY KEY,
    canonical_payload_fingerprint CHAR(64) NOT NULL,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE RESTRICT,
    execution_decision_id TEXT NOT NULL,
    work_unit_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL REFERENCES openair_dispatch(dispatch_id) ON DELETE RESTRICT,
    prior_capability_call_id TEXT NOT NULL,
    capability_call_id TEXT NOT NULL UNIQUE,
    attempt_number INTEGER NOT NULL,
    call_idempotency_key TEXT NOT NULL UNIQUE,
    provider_idempotency_key TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    expected_task_revision BIGINT NOT NULL,
    committed_task_revision BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (canonical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (attempt_number > 1 AND expected_task_revision >= 0 AND committed_task_revision > expected_task_revision),
    CHECK (call_idempotency_key <> provider_idempotency_key)
);

CREATE TABLE openair_recovery_directive (
    directive_id TEXT PRIMARY KEY,
    source_command_request_id TEXT NOT NULL UNIQUE REFERENCES openair_external_reconciliation(command_request_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE RESTRICT,
    source_execution_decision_id TEXT NOT NULL,
    source_work_unit_id TEXT NOT NULL,
    resolution_evidence_ref TEXT NOT NULL REFERENCES openair_external_evidence(evidence_id) ON DELETE RESTRICT,
    new_selection_request_id TEXT NOT NULL UNIQUE,
    expected_task_revision BIGINT NOT NULL,
    canonical_payload_fingerprint CHAR(64) NOT NULL,
    materialization_status TEXT NOT NULL DEFAULT 'pending',
    materialized_decision_id TEXT NULL,
    materialization_fingerprint CHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    materialized_at TIMESTAMPTZ NULL,
    CHECK (expected_task_revision >= 0),
    CHECK (canonical_payload_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (materialization_status IN ('pending', 'materialized', 'conflict')),
    CHECK ((materialization_status = 'materialized') = (materialized_at IS NOT NULL))
);
