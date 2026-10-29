# Testkit infrastructure tests

These tests protect the shared test infrastructure in `testkit/` itself. They do not claim a
Production protected outcome and do not issue a product or Verification verdict.

The PostgreSQL Harness checks cover isolated environment injection, fail-closed
dependency handling, cleanup after partial provisioning, the required no-skip
policy, and the pinned PostgreSQL profile. Product Suites consume this Harness
but remain responsible for their own Authority, evidence, and verdict rules.
