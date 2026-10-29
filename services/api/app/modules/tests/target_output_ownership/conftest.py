"""Current formal source with an isolated in-memory archive."""

import pytest
from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository


@pytest.fixture(scope="module")
def source():
    return ProductionDefinitionSource(
        archive=InMemoryRegistrySnapshotRepository(), planning_only=True
    )
