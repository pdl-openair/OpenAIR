# Artifact Review PostgreSQL probe - HOLD

Lifecycle status: **HOLD**.

The retained probe exercises two candidate long-term responsibilities:

1. the Artifact Review API rejects client-controlled reviewer/source identity;
2. an accepted review persists canonical state and server-derived identity in
   PostgreSQL and can be read through a fresh connection.

The current repository does not contain a formal Authority that defines those
API identity, state-transition, and durability requirements. In particular,
`contract://artifact-review-api` is not a traceable repository Authority, and
`docs/02_产品与Workflow/OpenAIR/待专项更新/task_board_and_artifact_management.md`
is explicitly not an established formal design.

Therefore this directory is diagnostic material, not a formal Suite. Its run
can provide implementation evidence but cannot produce a formal product PASS.
Promotion requires a formal Authority and a decision on whether the API input
boundary and PostgreSQL durability are one stable cross-component responsibility
or two independently owned responsibilities.

The weaker duplicate that used the incident DSN and could skip on missing
PostgreSQL was retired. This probe is the single retained candidate because it
uses the shared fail-closed PostgreSQL Harness and records isolated database
identity.
