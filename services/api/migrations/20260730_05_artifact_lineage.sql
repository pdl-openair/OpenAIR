CREATE TABLE IF NOT EXISTS openair_work_unit_lineage (
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE RESTRICT,
    work_unit_id TEXT NOT NULL,
    execution_decision_id TEXT NOT NULL,
    substage_instance_id TEXT NOT NULL,
    source_mode TEXT NOT NULL DEFAULT 'live',
    dispatchable BOOLEAN NOT NULL,
    migration_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (task_id, work_unit_id),
    UNIQUE (task_id, work_unit_id, source_mode),
    UNIQUE (task_id, work_unit_id, substage_instance_id, source_mode),
    UNIQUE (task_id, work_unit_id, execution_decision_id, substage_instance_id, source_mode),
    UNIQUE (migration_key),
    CHECK (source_mode IN ('live', 'legacy_projection')),
    CHECK ((source_mode = 'live') = dispatchable),
    CHECK (length(btrim(work_unit_id)) > 0),
    CHECK (length(btrim(execution_decision_id)) > 0),
    CHECK (length(btrim(substage_instance_id)) > 0),
    CHECK (migration_key LIKE '20260730_05:%')
);

CREATE TABLE IF NOT EXISTS openair_capability_call_lineage (
    task_id TEXT NOT NULL,
    capability_call_id TEXT NOT NULL,
    work_unit_id TEXT NOT NULL,
    substage_instance_id TEXT NOT NULL,
    source_mode TEXT NOT NULL DEFAULT 'live',
    migration_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (task_id, capability_call_id),
    UNIQUE (task_id, capability_call_id, work_unit_id, substage_instance_id, source_mode),
    UNIQUE (migration_key),
    FOREIGN KEY (task_id, work_unit_id, source_mode)
        REFERENCES openair_work_unit_lineage(task_id, work_unit_id, source_mode)
        ON DELETE RESTRICT,
    FOREIGN KEY (task_id, work_unit_id, substage_instance_id, source_mode)
        REFERENCES openair_work_unit_lineage(task_id, work_unit_id, substage_instance_id, source_mode)
        ON DELETE RESTRICT,
    CHECK (source_mode = 'live'),
    CHECK (length(btrim(capability_call_id)) > 0),
    CHECK (migration_key LIKE '20260730_05:%')
);

CREATE TABLE IF NOT EXISTS openair_artifact_lineage (
    task_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    work_unit_id TEXT NOT NULL,
    execution_decision_id TEXT NOT NULL,
    substage_instance_id TEXT NOT NULL,
    source_capability_call_id TEXT NULL,
    direct_work_unit_output BOOLEAN NOT NULL,
    source_mode TEXT NOT NULL DEFAULT 'live',
    migration_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (task_id, artifact_id),
    UNIQUE (migration_key),
    FOREIGN KEY (
        task_id, work_unit_id, execution_decision_id, substage_instance_id, source_mode
    ) REFERENCES openair_work_unit_lineage (
        task_id, work_unit_id, execution_decision_id, substage_instance_id, source_mode
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        task_id, source_capability_call_id, work_unit_id, substage_instance_id, source_mode
    ) REFERENCES openair_capability_call_lineage (
        task_id, capability_call_id, work_unit_id, substage_instance_id, source_mode
    ) ON DELETE RESTRICT,
    CHECK (source_mode = 'live'),
    CHECK ((source_capability_call_id IS NULL) = direct_work_unit_output),
    CHECK (length(btrim(artifact_id)) > 0),
    CHECK (migration_key LIKE '20260730_05:%')
);

CREATE TABLE IF NOT EXISTS openair_artifact_lineage_audit (
    audit_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    migration_key TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (task_id, artifact_id)
        REFERENCES openair_artifact_lineage(task_id, artifact_id) ON DELETE RESTRICT,
    UNIQUE (migration_key),
    CHECK (audit_key LIKE '20260730_05:%'),
    CHECK (migration_key LIKE '20260730_05:%')
);

CREATE INDEX IF NOT EXISTS idx_openair_artifact_lineage_execution
    ON openair_artifact_lineage(task_id, execution_decision_id, substage_instance_id, artifact_id);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM openair_tasks AS task
          CROSS JOIN LATERAL jsonb_to_recordset(COALESCE(task.payload -> 'artifacts', '[]'::jsonb)) AS artifact(
              "lineageKind" TEXT
          )
         WHERE artifact."lineageKind" = 'planning'
    ) THEN
        RAISE EXCEPTION 'planning Artifact cannot become a live research Artifact';
    END IF;
END $$;

INSERT INTO openair_work_unit_lineage (
    task_id, work_unit_id, execution_decision_id, substage_instance_id,
    source_mode, dispatchable, migration_key
)
SELECT
    task.task_id,
    unit."workUnitId",
    unit."executionDecisionId",
    unit."substageInstanceId",
    COALESCE(unit."sourceMode", 'live'),
    COALESCE((unit."dispatchable")::boolean, FALSE),
    '20260730_05:work_unit:' || task.task_id || ':' || unit."workUnitId"
FROM openair_tasks AS task
CROSS JOIN LATERAL jsonb_to_recordset(COALESCE(task.payload -> 'workUnits', '[]'::jsonb)) AS unit(
    "workUnitId" TEXT,
    "executionDecisionId" TEXT,
    "substageInstanceId" TEXT,
    "sourceMode" TEXT,
    "dispatchable" TEXT
)
WHERE unit."workUnitId" IS NOT NULL
  AND unit."executionDecisionId" IS NOT NULL
  AND unit."substageInstanceId" IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO openair_capability_call_lineage (
    task_id, capability_call_id, work_unit_id, substage_instance_id, source_mode, migration_key
)
SELECT
    task.task_id,
    call."capabilityCallId",
    call."workUnitId",
    call."substageInstanceId",
    'live',
    '20260730_05:call:' || task.task_id || ':' || call."capabilityCallId"
FROM openair_tasks AS task
CROSS JOIN LATERAL jsonb_to_recordset(COALESCE(task.payload -> 'capabilityCalls', '[]'::jsonb)) AS call(
    "capabilityCallId" TEXT,
    "workUnitId" TEXT,
    "substageInstanceId" TEXT
)
JOIN openair_work_unit_lineage AS unit
  ON unit.task_id = task.task_id
 AND unit.work_unit_id = call."workUnitId"
 AND unit.substage_instance_id = call."substageInstanceId"
 AND unit.source_mode = 'live'
WHERE call."capabilityCallId" IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO openair_artifact_lineage (
    task_id, artifact_id, work_unit_id, execution_decision_id, substage_instance_id,
    source_capability_call_id, direct_work_unit_output, source_mode, migration_key
)
SELECT
    task.task_id,
    artifact."artifactId",
    artifact."workUnitId",
    unit.execution_decision_id,
    artifact."substageInstanceId",
    artifact."sourceCapabilityCallId",
    artifact."sourceCapabilityCallId" IS NULL,
    'live',
    '20260730_05:artifact:' || task.task_id || ':' || artifact."artifactId"
FROM openair_tasks AS task
CROSS JOIN LATERAL jsonb_to_recordset(COALESCE(task.payload -> 'artifacts', '[]'::jsonb)) AS artifact(
    "artifactId" TEXT,
    "workUnitId" TEXT,
    "substageInstanceId" TEXT,
    "sourceCapabilityCallId" TEXT
)
JOIN openair_work_unit_lineage AS unit
  ON unit.task_id = task.task_id
 AND unit.work_unit_id = artifact."workUnitId"
 AND unit.substage_instance_id = artifact."substageInstanceId"
 AND unit.source_mode = 'live'
LEFT JOIN openair_capability_call_lineage AS call
  ON call.task_id = task.task_id
 AND call.capability_call_id = artifact."sourceCapabilityCallId"
 AND call.work_unit_id = artifact."workUnitId"
 AND call.substage_instance_id = artifact."substageInstanceId"
 AND call.source_mode = 'live'
WHERE artifact."artifactId" IS NOT NULL
  AND (artifact."sourceCapabilityCallId" IS NULL OR call.capability_call_id IS NOT NULL)
ON CONFLICT DO NOTHING;

INSERT INTO openair_artifact_lineage_audit (audit_key, task_id, artifact_id, migration_key)
SELECT
    lineage.migration_key || ':backfill',
    lineage.task_id,
    lineage.artifact_id,
    lineage.migration_key
FROM openair_artifact_lineage AS lineage
ON CONFLICT DO NOTHING;
