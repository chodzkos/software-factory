#!/usr/bin/env bash
set -euo pipefail

DISPATCHER_PROFILE="${DISPATCHER_PROFILE:-default}"

if ! command -v hermes >/dev/null 2>&1; then
  echo "ERROR: hermes not found in PATH" >&2
  exit 1
fi

# Software Factory używa własnego profilu task-decomposer; wyłączamy drugi, wbudowany decomposer Hermesa.
hermes -p "${DISPATCHER_PROFILE}" config set kanban.auto_decompose false

# Orchestrator ma jawnie tworzyć taski i dostać informację zwrotną po ich zakończeniu.
hermes -p "${DISPATCHER_PROFILE}" config set kanban.auto_subscribe_on_create true
hermes -p "${DISPATCHER_PROFILE}" config set kanban.orchestrator_profile orchestrator
hermes -p "${DISPATCHER_PROFILE}" config set kanban.default_assignee routing-sink

echo "Kanban Software Factory configured on profile: ${DISPATCHER_PROFILE}"
echo "auto_decompose=false"
echo "auto_subscribe_on_create=true"
echo "orchestrator_profile=orchestrator"
echo "default_assignee=routing-sink"
