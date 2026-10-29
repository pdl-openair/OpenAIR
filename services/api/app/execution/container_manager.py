"""Docker container lifecycle management for OpenAIR workers.

Uses the ``docker`` Python SDK to launch, monitor, and clean up worker
containers. Each container runs a single task execution and is removed
after completion.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class ContainerManager:
    """Manages Docker container lifecycle for worker execution."""

    def __init__(self) -> None:
        self._client: Any = None
        self._active: dict[str, str] = {}  # execution_id → container_id

    @property
    def client(self) -> Any:
        """Lazy-initialised Docker client."""
        if self._client is None:
            try:
                import docker
                docker_host = getattr(settings, "docker_socket", "") or os.environ.get("DOCKER_HOST", "")
                kwargs: dict[str, Any] = {}
                if docker_host:
                    kwargs["base_url"] = docker_host
                self._client = docker.from_env(**kwargs)
                logger.info("Docker client initialised")
            except ImportError:
                raise RuntimeError(
                    "The 'docker' Python package is required for container runtime. "
                    "Install it with: pip install docker"
                )
            except Exception as exc:
                raise RuntimeError(f"Failed to initialise Docker client: {exc}") from exc
        return self._client

    def launch(
        self,
        task_id: str,
        execution_id: str,
        env_vars: dict[str, str],
        workspace_bind: str | None = None,
        *,
        owner_id: str = "",
        keepalive: bool = False,
    ) -> str:
        """Launch a worker container for a task execution.

        Parameters
        ----------
        task_id : str
            The task being executed.
        execution_id : str
            Unique ID for this execution run (first turn's id for keepalive workers).
        env_vars : dict[str, str]
            Environment variables to pass to the container.
        workspace_bind : str | None
            Host path to bind-mount as /workspace inside the container.
            If None, a Docker volume is used.
        owner_id : str
            Task owner (tenant). Recorded as a label so the reaper can enforce
            the per-user cap and so audits can attribute containers to tenants.
        keepalive : bool
            Whether this container runs in long-lived server mode (warm worker).
            Recorded as a label to distinguish from one-shot workers.

        Returns
        -------
        str
            Container ID.
        """
        image = getattr(settings, "worker_image", "openair-worker:latest")
        cpu_limit = getattr(settings, "worker_cpu_limit", 1.0)
        mem_limit = getattr(settings, "worker_memory_limit", "2g")

        volumes: dict[str, dict[str, str]] = {}

        workspace_volume = getattr(settings, "worker_workspace_volume", "").strip()
        if workspace_volume:
            # Compose/cloud: mount the SHARED named volume. In DinD (API container
            # launches workers via docker.sock) a bind-mount path is resolved by the
            # daemon against ITS OWN filesystem, not the API container's — so the
            # API and the worker would see different directories and files written by
            # the worker would never reach the API (artifacts never persisted). A
            # named volume is shared by name and is the only correct way here.
            volumes[workspace_volume] = {"bind": "/workspace", "mode": "rw"}
        elif workspace_bind:
            # Local-dev fallback: API and docker daemon share the host filesystem.
            volumes[workspace_bind] = {"bind": "/workspace", "mode": "rw"}

        # Always set container-level env vars
        container_env = dict(env_vars)
        container_env.setdefault("OPENAIR_WORKSPACE_ROOT", "/workspace")

        # Keepalive workers are one-per-task and outlive individual execution_ids,
        # so name them by task_id (stable). One-shot workers are unique per turn —
        # use the full execution_id (never truncate: `execution_id[:12]` dropped
        # the uuid for short task_ids, making names collide across turns).
        if keepalive:
            container_name = keepalive_container_name(task_id)
        else:
            container_name = f"openair-worker-{execution_id}"

        worker_network = getattr(settings, "worker_network", "").strip()
        if worker_network:
            # Compose 部署：worker 加入命名用户网络，按服务名解析 api/minio（嵌入式 DNS）。
            network_kwargs: dict[str, Any] = {"network": worker_network}
        else:
            # 本地开发（API 在宿主机）：回退默认 bridge，靠 host.docker.internal 回宿主机。
            network_kwargs = {"network_mode": "bridge"}

        logger.info(
            "launching container name=%s image=%s cpu=%s mem=%s network=%s",
            container_name, image, cpu_limit, mem_limit, worker_network or "bridge",
        )

        try:
            labels: dict[str, str] = {
                "openair.task_id": task_id,
                "openair.execution_id": execution_id,
                "openair.managed": "true",
                "openair.keepalive": "true" if keepalive else "false",
            }
            if owner_id:
                labels["openair.owner_id"] = owner_id

            # All these are convenience kwargs that docker-py routes into the container
            # config / HostConfig automatically (mem_limit, extra_hosts, auto_remove, and
            # network* are all accepted by the high-level run()). NOTE: workspace-isolation
            # gid is NOT passed here — Docker's group_add is discarded by su-exec on
            # privilege drop, so the gid is conveyed via env (OPENAIR_WORKER_TASK_GID) and
            # applied as the primary gid by the worker entrypoint.
            run_kwargs: dict[str, Any] = {
                "image": image,
                "name": container_name,
                "environment": container_env,
                "volumes": volumes,
                "detach": True,
                "mem_limit": mem_limit,
                "extra_hosts": {"host.docker.internal": "host-gateway"},
                "auto_remove": False,  # we manage cleanup ourselves
                "labels": labels,
                **network_kwargs,
            }
            container = self.client.containers.run(**run_kwargs)
        except Exception as exc:
            raise RuntimeError(f"Failed to launch worker container: {exc}") from exc

        container_id = container.id
        self._active[execution_id] = container_id
        logger.info("container launched: id=%s name=%s", container_id[:12], container_name)
        return container_id

    def get_status(self, execution_id: str) -> str | None:
        """Check container status.

        Returns one of: "running", "exited", "dead", "removing", or None if not found.
        """
        container_id = self._active.get(execution_id)
        if not container_id:
            return None
        try:
            container = self.client.containers.get(container_id)
            return container.status
        except Exception:
            return None

    def get_logs(self, execution_id: str, tail: int = 100) -> str:
        """Fetch container logs."""
        container_id = self._active.get(execution_id)
        if not container_id:
            return ""
        try:
            container = self.client.containers.get(container_id)
            return container.logs(tail=tail).decode("utf-8", errors="replace")
        except Exception as exc:
            return f"<failed to fetch logs: {exc}>"

    def stop(self, execution_id: str, timeout: int = 10) -> None:
        """Stop a running container."""
        container_id = self._active.get(execution_id)
        if not container_id:
            return
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=timeout)
            logger.info("container stopped: id=%s", container_id[:12])
        except Exception as exc:
            logger.warning("failed to stop container %s: %s", execution_id, exc)

    def cleanup(self, execution_id: str) -> None:
        """Remove a stopped container."""
        container_id = self._active.pop(execution_id, None)
        if not container_id:
            return
        try:
            container = self.client.containers.get(container_id)
            container.remove(force=True)
            logger.info("container removed: id=%s", container_id[:12])
        except Exception as exc:
            logger.warning("failed to remove container %s: %s", execution_id, exc)

    def stop_and_cleanup(self, execution_id: str) -> None:
        """Stop and remove a container."""
        self.stop(execution_id)
        self.cleanup(execution_id)

    # ------------------------------------------------------------------
    # container_id-keyed operations (used by keepalive pool / reaper)
    # ------------------------------------------------------------------
    # Keepalive workers serve many execution_ids over their lifetime, so the
    # execution_id-keyed ``_active`` map is insufficient for them. These
    # methods act directly on a container id and are used by the worker pool
    # and the reaper.

    def stop_and_remove_container(self, container_id: str, timeout: int = 10) -> None:
        """Stop (if running) and force-remove a container by id."""
        try:
            container = self.client.containers.get(container_id)
            if container.status == "running":
                container.stop(timeout=timeout)
            container.remove(force=True)
            logger.info("container removed by id: %s", container_id[:12])
        except Exception as exc:
            logger.warning("failed to stop/remove container %s: %s", container_id[:12], exc)

    def remove_container_by_name(self, name: str) -> bool:
        """Force-remove a container by name. Returns True if removed.

        No-op (returns False) if no such container exists. Used to clear stale
        same-named containers before a fresh launch so Docker doesn't 409.
        """
        try:
            container = self.client.containers.get(name)
        except Exception:
            return False
        try:
            container.remove(force=True)
            logger.info("removed stale container by name: %s", name)
            return True
        except Exception as exc:
            logger.warning("failed to remove container %s: %s", name, exc)
            return False

    def remove_all_managed(self) -> int:
        """Force-remove every ``openair.managed`` container (incl. exited).

        Used by startup reconciliation to clear orphan workers left by a
        previous/crashed API process. Returns the number removed.
        """
        removed = 0
        try:
            containers = self.client.containers.list(
                all=True, filters={"label": "openair.managed=true"}
            )
        except Exception as exc:
            logger.warning("failed to list managed containers: %s", exc)
            return 0
        for c in containers:
            try:
                c.remove(force=True)
                removed += 1
            except Exception as exc:
                logger.warning("startup reconcile: failed to remove %s: %s", c.short_id, exc)
        return removed

    def list_active(self) -> list[dict[str, str]]:
        """List all active worker containers managed by OpenAIR."""
        try:
            containers = self.client.containers.list(
                filters={"label": "openair.managed=true"},
            )
            return [
                {
                    "id": c.short_id,
                    "name": c.name,
                    "status": c.status,
                    "task_id": (c.labels or {}).get("openair.task_id", ""),
                    "execution_id": (c.labels or {}).get("openair.execution_id", ""),
                }
                for c in containers
            ]
        except Exception as exc:
            logger.warning("failed to list active containers: %s", exc)
            return []


# Module-level singleton
_manager: ContainerManager | None = None


def keepalive_container_name(task_id: str) -> str:
    """Stable container name for a task's warm (keepalive) worker.

    There is at most one warm worker per task, and it outlives individual
    execution_ids, so the name is derived from task_id (not execution_id) and is
    stable across the worker's life. Also used to locate/evict stale same-named
    containers before a fresh launch.
    """
    return f"openair-worker-task-{task_id}"


def get_container_manager() -> ContainerManager:
    """Return the singleton container manager."""
    global _manager
    if _manager is None:
        _manager = ContainerManager()
    return _manager
