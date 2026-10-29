#!/bin/sh
set -e

# Claude Code refuses --dangerously-skip-permissions (bypassPermissions) when run
# as root/sudo. The worker image therefore runs the actual process as an
# unprivileged `openair` user. This entrypoint performs the few operations that
# need root at container start — fixing ownership of the bind-mounted workspace
# volume and the runtime Claude config dir — then drops privileges before exec.

# Workspace is a bind-mount: its ownership is whatever the host/dockerd set.
# Make it writable by openair (no-op on Docker Desktop's relaxed file sharing).
#
# Phase-2 #3: under OPENAIR_WORKSPACE_ISOLATION=1 the API has set per-task ownership
# (root:<gid> 0770) and the worker runs with that gid as its PRIMARY gid — so the recursive
# chown is deliberately SKIPPED: it would clobber the per-task ownership AND re-owner every
# sibling task dir on the shared volume (re-opening cross-tenant reads).
# Defense-in-depth: skip the chown ONLY when isolation is fully armed — flag set AND a gid
# conveyed. The flag without a gid is a broken half-state (the API declined isolation but
# advertised it); in that case run the legacy chown so the worker still gets a writable
# workspace instead of starting up unwritable and failing the task.
if [ -d /workspace ] && { [ "${OPENAIR_WORKSPACE_ISOLATION}" != "1" ] || [ -z "${OPENAIR_WORKER_TASK_GID}" ]; }; then
    chown -R openair:openair /workspace 2>/dev/null || true
fi
# ~/.claude holds Claude CLI runtime state (config, projects); ensure writable.
if [ -d /home/openair/.claude ]; then
    chown -R openair:openair /home/openair/.claude 2>/dev/null || true
fi

# Phase-2 #3: when workspace isolation is on, the API sets each task dir to root:<gid> 0770
# and passes that gid via OPENAIR_WORKER_TASK_GID. Drop to openair with that PRIMARY gid.
# (Docker's group_add is useless here: su-exec re-initialises supplementary groups from
# /etc/group, discarding group_add — so we set the gid as the primary via su-exec, which
# accepts a numeric gid. Verified: `su-exec openair:<gid>` → uid=1000 gid=<gid>.) Unset ⇒
# legacy openair:openair.
exec su-exec "openair:${OPENAIR_WORKER_TASK_GID:-openair}" "$@"
