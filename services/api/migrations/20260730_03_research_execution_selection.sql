CREATE TABLE openair_execution_selection_request (
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    selection_request_id TEXT NOT NULL,
    command_hash TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    execution_decision_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (task_id, owner_id, workspace_id, selection_request_id),
    CHECK (length(btrim(owner_id)) > 0),
    CHECK (length(btrim(workspace_id)) > 0),
    CHECK (length(btrim(selection_request_id)) > 0),
    CHECK (command_hash ~ '^sha256:[0-9a-f]{64}$'),
    CHECK (payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    CHECK (length(btrim(execution_decision_id)) > 0)
);
CREATE TABLE openair_equivalent_candidate_fairness (
    policy_version TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    registry_snapshot_content_hash TEXT NOT NULL,
    substage_definition_ref TEXT NOT NULL,
    equivalence_set_hash TEXT NOT NULL,
    next_slot BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        policy_version,
        owner_id,
        workspace_id,
        registry_snapshot_content_hash,
        substage_definition_ref,
        equivalence_set_hash
    ),
    CHECK (length(btrim(policy_version)) > 0),
    CHECK (length(btrim(owner_id)) > 0),
    CHECK (length(btrim(workspace_id)) > 0),
    CHECK (registry_snapshot_content_hash ~ '^sha256:[0-9a-f]{64}$'),
    CHECK (length(btrim(substage_definition_ref)) > 0),
    CHECK (equivalence_set_hash ~ '^sha256:[0-9a-f]{64}$'),
    CHECK (next_slot >= 0)
);
