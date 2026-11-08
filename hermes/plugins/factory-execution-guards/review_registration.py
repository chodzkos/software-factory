"""Tożsamość profilowej rejestracji adaptera; nie zastępuje bramki startu workera."""
from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import handoff as h
from . import review_intent as ri

_LOG = logging.getLogger(__name__)
_NAME = "kanban_request_review"
_PROFILE = "coder-claude"
_ACTIVE: dict[str, RegistrationBinding] = {}


def _metadata(entry: Any) -> bytes:
    """Porównaj także schemat i metadane, a nie samą nazwę narzędzia."""
    if (entry.name != _NAME or entry.toolset != "kanban"
            or type(entry.schema) is not dict
            or entry.schema.get("name") != _NAME
            or type(entry.requires_env) is not list
            or any(type(item) is not str for item in entry.requires_env)
            or entry.is_async is not False
            or type(entry.description) is not str or type(entry.emoji) is not str
            or entry.max_result_size_chars is not None
            or entry.dynamic_schema_overrides is not None):
        # PluginContext tego wydania nie przekazuje dwóch ostatnich opcji.
        # Nie wolno ich zgubić po cichu przy przyszłej zmianie natywnego toola.
        raise h.HandoffError("native review registration metadata unsupported")
    return json.dumps({"name": entry.name, "toolset": entry.toolset,
                       "schema": entry.schema, "requires_env": entry.requires_env,
                       "is_async": entry.is_async, "description": entry.description,
                       "emoji": entry.emoji}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _scope(ctx: Any, registry: Any) -> str:
    """Nie utożsamiaj nazwy profilu ze sprawdzonym zakresem rejestru."""
    scope = registry.current_scope_key()
    if (os.environ.get("HERMES_PROFILE", "").strip() != _PROFILE
            or ctx.profile_name != _PROFILE
            or type(scope) is not str or not scope
            or ctx._manager.scope_key != scope):
        raise h.HandoffError("native review registration profile/scope mismatch")
    path = Path(scope)
    if (not path.is_absolute() or not path.is_dir()
            or str(path.resolve(strict=True)) != scope):
        raise h.HandoffError("native review registration scope is not canonical")
    return scope


@dataclass
class RegistrationBinding:
    """Prywatne powiązanie uchwytu z jednym wpisem; nie jest durable authority."""
    ctx: Any
    registry: Any
    scope: str
    native_entry: Any
    native: Callable[..., Any]
    check_fn: Callable[..., Any]
    metadata: bytes
    guard: Any
    handler: Callable[..., Any] | None = None
    entry: Any = None
    tool_handle: Any = None
    unload_handle: Any = None
    alive: bool = True

    def revoke(self) -> None:
        """Wyładowanie lub odmowa kończy generację i unieważnia zamiar handoffu."""
        self.alive = False
        if _ACTIVE.get(self.scope) is self:
            _ACTIVE.pop(self.scope, None)
        ri.invalidate()
        # Rejestracja należy wyłącznie do tego modułu/profilu. Po zmianie scope
        # nie wybieraj klucza z potencjalnie innego środowiska wywołującego.
        for records in (self.guard._PENDING_ATTESTATIONS,
                        self.guard._COMPLETED_ATTESTATIONS):
            for key in tuple(records):
                if key[0] == _PROFILE:
                    records.pop(key, None)

    def verify(self) -> None:
        """Sprawdź rzeczywisty lokalny slot, globalny oryginał i widok aktywny."""
        if not self.alive or _scope(self.ctx, self.registry) != self.scope:
            raise h.HandoffError("native review registration is inactive")
        # Bez scope snapshot_registration czyta globalny slot, nie fallback.
        original = self.registry.snapshot_registration(_NAME)
        local = self.registry.snapshot_registration(_NAME, scope=self.scope)
        effective = self.registry.get_entry(_NAME, scope=self.scope)
        if (original is not self.native_entry or original.handler is not self.native
                or original.check_fn is not self.check_fn
                or _metadata(original) != self.metadata
                or local is None or local is not self.entry or effective is not local
                or local.handler is not self.handler or local.check_fn is not self.check_fn
                or _metadata(local) != self.metadata):
            raise h.HandoffError("native review registration identity changed")
        matches = [e for e in self.registry.get_all_entries() if e.name == _NAME]
        if len(matches) != 1 or matches[0] is not local:
            raise h.HandoffError("native review registration active view differs")
        if self.tool_handle is None or not callable(getattr(self.tool_handle, "dispose", None)):
            raise h.HandoffError("native review registration has no lifecycle handle")


def verify_active_registration() -> None:
    """Sonda TEGO procesu; przyszły launcher musi wywołać ją przed pracą modelu."""
    from tools.registry import registry
    try:
        binding = _ACTIVE[registry.current_scope_key()]
        if binding.registry is not registry:
            raise h.HandoffError("native review registry object changed")
        binding.verify()
    except Exception:
        # Nie zwracaj zielonego wyniku dla samego pliku/konfiguracji pluginu.
        raise h.HandoffError("active native review adapter is not verified") from None


def register(ctx: Any, guard: Any, native: Callable[..., Any],
             check_fn: Callable[..., Any], execute: Callable[..., str]) -> None:
    """Zarejestruj jeden override i potwierdź jego rzeczywistą tożsamość."""
    from tools.registry import registry, tool_error

    scope = _scope(ctx, registry)
    original = registry.snapshot_registration(_NAME)
    if (original is None or registry.get_entry(_NAME, scope=scope) is not original
            or registry.snapshot_registration(_NAME, scope=scope) is not None
            or original.handler is not native or original.check_fn is not check_fn):
        raise h.HandoffError("native review original registration is not exact")
    metadata = _metadata(original)
    binding = RegistrationBinding(ctx, registry, scope, original, native, check_fn,
                                  metadata, guard)

    def handle(args: Any, **kwargs: Any) -> str:
        try:
            # Wywołanie zachowanego starego handlera też musi przejść tę kontrolę.
            from tools.registry import registry as current_registry
            if current_registry is not binding.registry or _ACTIVE.get(binding.scope) is not binding:
                raise h.HandoffError("native review registry object changed")
            binding.verify()
        except Exception as exc:
            binding.revoke()
            _LOG.warning("factory review registration refused (%s); no automatic retry",
                         type(exc).__name__)
            return tool_error("factory review adapter inactive; inspect retained state, do not retry automatically")
        return execute(native, args, guard, **kwargs)

    binding.handler = handle
    try:
        binding.tool_handle = ctx.register_tool(
            name=original.name, toolset=original.toolset,
            schema=copy.deepcopy(original.schema), handler=handle,
            check_fn=check_fn, requires_env=list(original.requires_env),
            is_async=original.is_async, description=original.description,
            emoji=original.emoji, override=True)
        binding.entry = registry.snapshot_registration(_NAME, scope=scope)
        binding.verify()
        binding.unload_handle = ctx.on_unload(binding.revoke)
        if (binding.unload_handle is None
                or not callable(getattr(binding.unload_handle, "dispose", None))):
            raise h.HandoffError("native review unload registration failed")
        binding.verify()
        _ACTIVE[scope] = binding
    except Exception:
        binding.revoke()
        # Dysponowanie natywnymi uchwytami respektuje mechanikę ich generacji;
        # brak bezpośredniego deregister albo nadpisania cudzego nowego wpisu.
        for handle_ref in (binding.unload_handle, binding.tool_handle):
            try:
                if handle_ref is not None:
                    handle_ref.dispose()
            except Exception as exc:
                _LOG.warning("factory review registration cleanup failed (%s)", type(exc).__name__)
        raise h.HandoffError("scoped native review adapter registration failed") from None
