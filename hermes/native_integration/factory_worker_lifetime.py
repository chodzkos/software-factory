"""Obowiązkowe związanie coder/reviewer/release z generacją sprawdzoną przy starcie.

Nie jest piaskownicą dla dowolnego kodu Pythona w tym samym interpreterze.
Blokada discovery obejmuje rzeczywisty handler i obowiązkowe hooki: reload
z innego wątku czeka na zakończenie operacji, nigdy nie podmienia jej ochrony.
"""
from __future__ import annotations

import contextvars
import copy
import functools
import hashlib
import inspect
import json
import os
import threading
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable

_STATES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_SCOPES: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
# Utrata ostatniej referencji do agenta nie przywraca opcjonalnej ochrony.
_PROTECTED_SCOPES: set[str] = set()
_MAP_LOCK = threading.RLock()
_CURRENT: contextvars.ContextVar = contextvars.ContextVar('factory_worker_operation', default=None)


class WorkerLifetimeError(RuntimeError):
    """Utracona gotowość oznacza zatrzymanie; wynik operacji wymaga inspekcji."""


def _fail(reason: str) -> None:
    raise WorkerLifetimeError('FACTORY_WORKER_LIFETIME_REFUSED: ' + reason)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _protected() -> bool:
    from factory_profile_start import selected_profile
    from hermes_cli.profiles import get_active_profile_name
    return (selected_profile() is not None
            or get_active_profile_name() in {'coder-claude', 'reviewer-gpt'})


class WorkerLifetime:
    """Prywatny uchwyt obiektu agenta; konfiguracja nie może odnowić generacji."""

    def __init__(self, agent: Any, proof: Any) -> None:
        self.agent = weakref.ref(agent)
        self.proof = proof
        self.revoked = threading.Event()
        self.failure_reason = ""
        # Lock order: discovery -> review intent -> decision.  Revocation takes
        # ONLY decision, never discovery/intent and never waits on a handler.
        # Held across the final database COMMIT, not SQL preparation, receipt
        # construction, filesystem work, executor waits, hooks or network.
        self.decision_lock = threading.RLock()
        self.pre = proof.module.on_pre_tool_call
        self.post = proof.module.on_post_tool_call
        # Both required plugins run as mandatory hooks. Suppressing the optional
        # manager observers must NOT drop the readonly plugin's pre-hook.
        readonly = getattr(proof, 'readonly_module', None)
        self.extra_pre = () if readonly is None else ((readonly, readonly.on_pre_tool_call),)
        self.tools = _json(agent.tools)
        self.definitions = {t['function']['name']: _json(t) for t in agent.tools}
        self.entries = {n: proof.registry.get_entry(n) for n in self.definitions}
        self.handlers = {n: e.handler for n, e in self.entries.items()}
        self.payloads: set[str] = set()
        self.payload_lock = threading.Lock()

    def revoke(self, reason: str) -> None:
        """Zachowaj pierwszą odmowę, nawet gdy native executor opakuje wyjątek."""
        with self.decision_lock:
            if not self.revoked.is_set():
                self.failure_reason = reason
            self.revoked.set()

    def require_active(self) -> None:
        """Check revocation without waiting for the serialized running handler."""
        if self.revoked.is_set():
            _fail('worker generation revoked; ' + self.failure_reason
                  + '; effects may exist, inspect state, do not retry automatically')

    def verify(self) -> None:
        """Błąd jest trwały; przywrócenie hooka lub pliku nie odnawia workera."""
        try:
            self.require_active()
            self.proof.verify()
            agent = self.agent()
            from factory_model_boundary import is_moa_provider
            if is_moa_provider(getattr(agent, "provider", None)):
                _fail("MoA route is unsupported for the protected worker")
            if (agent is None or agent.valid_tool_names != set(self.definitions)
                    or _json(agent.tools) != self.tools):
                _fail('worker tool definitions changed')
            if (self.proof.module.on_pre_tool_call is not self.pre
                    or self.proof.module.on_post_tool_call is not self.post):
                _fail('mandatory callback identity changed')
            if any(module.on_pre_tool_call is not callback for module, callback in self.extra_pre):
                _fail('mandatory readonly callback identity changed')
            for name, entry in self.entries.items():
                if (self.proof.registry.get_entry(name) is not entry
                        or entry.handler is not self.handlers[name]
                        or entry.is_async is not False):
                    _fail('bound handler identity changed: ' + name)
        except Exception as exc:
            self.revoke(str(exc) if isinstance(exc, WorkerLifetimeError) else 'worker proof verification failed')
            if isinstance(exc, WorkerLifetimeError):
                raise
            raise WorkerLifetimeError('FACTORY_WORKER_LIFETIME_REFUSED: ' + str(exc)) from exc

    def verify_tools(self, tools: Any) -> None:
        """Dopuszczaj tylko niezmienione podzbiory sprawdzonych definicji."""
        try:
            if type(tools) is not list:
                _fail('model capability list is invalid')
            names = [t['function']['name'] for t in tools]
            if (len(names) != len(set(names))
                    or any(self.definitions.get(n) != _json(t) for n, t in zip(names, tools))):
                _fail('outgoing model capabilities changed')
        except Exception as exc:
            self.revoke(str(exc) if isinstance(exc, WorkerLifetimeError) else 'worker proof verification failed')
            if isinstance(exc, WorkerLifetimeError):
                raise
            raise WorkerLifetimeError('FACTORY_WORKER_LIFETIME_REFUSED: model capability shape') from exc

    def envelope(self, payload: Any, allowed_names: set[str] | None = None) -> str:
        """Semantyka po transformacji, nie sam fakt wywołania buildera."""
        from factory_model_boundary import capabilities
        try:
            value = capabilities(self.agent(), payload, self.definitions, allowed_names)
            return hashlib.sha256(_json(value).encode()).hexdigest()
        except Exception as exc:
            self.revoke('model capability validation failed: ' + str(exc))
            raise WorkerLifetimeError('FACTORY_WORKER_LIFETIME_REFUSED: ' + str(exc)) from exc


def bind_worker(agent: Any, proof: Any) -> None:
    """Zapisz sprawdzony stan poza modyfikowalnymi polami agenta."""
    with proof.manager._discovery_lock:
        proof.verify()
        state = WorkerLifetime(agent, proof)
        state.verify()
        with _MAP_LOCK:
            if agent in _STATES or str(proof.home) in _SCOPES:
                _fail('a protected worker already owns this profile generation')
            _STATES[agent] = state
            _SCOPES[str(proof.home)] = state
            _PROTECTED_SCOPES.add(str(proof.home))


def state_for(agent: Any) -> WorkerLifetime | None:
    """Zmiana środowiska nie usuwa przypisania już utworzonego workera."""
    with _MAP_LOCK:
        try:
            state = _STATES.get(agent)
        except TypeError:
            state = None
    if state is None and _protected():
        _fail('protected agent has no constructor proof')
    return state


def check_agent(agent: Any) -> WorkerLifetime | None:
    """Krótka kontrola przed wejściem w kolejną fazę, także ścieżkę modelu."""
    state = state_for(agent)
    if state is not None:
        state.require_active()
        with state.proof.manager._discovery_lock:
            state.verify()
    return state


def abandon_worker(agent: Any, reason: str) -> bool:
    """Permanently revoke a bound worker; other profiles retain native behavior.

    A COMMIT already holding the decision lock finishes first.  Otherwise no
    subsequent protected admission or review COMMIT may win.  Already admitted
    arbitrary handlers are NOT cancellable/rollbackable by this fence.
    """
    with _MAP_LOCK:
        try:
            state = _STATES.get(agent)
        except TypeError:
            state = None
    if state is None:
        return False
    state.revoke(reason)
    return True


def check_moa_turn(agent: Any, moa_config: Any) -> None:
    """Refuse opt-in advisor fan-out before turn setup or aggregate callbacks.

    Normal Hermes profiles keep their native MoA behavior. A bound coder's
    refusal is permanent, as for every unsupported runtime capability; it is
    not a request retry mechanism and does not undo prior admitted effects.
    """
    state = check_agent(agent)
    if state is not None and moa_config:
        state.revoke("MoA turn is unsupported for the protected worker")
        state.require_active()


def close_worker(agent: Any) -> None:
    """Close does not wait for a handler; it is ordered against final COMMIT."""
    abandon_worker(agent, 'agent closed')


@dataclass
class ToolOperation:
    """Jedno użycie także po skopiowaniu ContextVar do innego wątku."""
    state: WorkerLifetime
    name: str
    metadata: dict[str, Any]
    used: bool = False
    admitted: bool = False
    finished: bool = False
    thread_id: int | None = None
    lock: Any = field(default_factory=threading.Lock)


def review_operation(guard: Any) -> ToolOperation:
    """Require the actual admitted review handler, not a raw adapter call."""
    op = _CURRENT.get()
    if (op is None or op.name != 'kanban_request_review' or not op.used
            or not op.admitted or op.finished
            or op.thread_id != threading.get_ident()
            or op.state.proof.module._guard is not guard):
        _fail('review has no current admitted worker operation')
    op.state.verify()
    return op


def commit_review(op: ToolOperation, conn: Any, guard: Any) -> None:
    """Serialize final COMMIT with close/timeout/interrupt, not the whole tool.

    The caller holds discovery and the review-intent commit window. All SQL,
    receipt writes and revalidation precede this method. There are no user
    hooks or application callbacks inside the decision critical section.
    A commit that won cannot be undone by a later close or lost response.
    """
    if review_operation(guard) is not op:
        _fail('review operation changed before commit')
    with op.state.decision_lock:
        op.state.require_active()
        try:
            conn.execute('COMMIT')
        except BaseException:
            # SQLite may have committed before acknowledgement was lost. A
            # refusal is NOT rollback evidence and the agent must not retry.
            op.state.revoke('handoff COMMIT failed or acknowledgement lost; outcome unknown')
            raise



def approval_operation(callback: Callable) -> ToolOperation:
    """Require this thread's admitted reviewer approval, not a standalone call.

    Standalone read-only receipt/approval consumers do not use this API. The
    function identity binds the caller to the actual loaded handoff module.
    """
    op = _CURRENT.get()
    if (op is None or op.name != 'factory_review_approve' or not op.used
            or not op.admitted or op.finished or op.thread_id != threading.get_ident()
            or getattr(op.state.proof, 'profile', None) != 'reviewer-gpt'
            or op.state.proof.module._handoff.guarded_reviewer_complete is not callback):
        _fail('approval has no current admitted reviewer operation')
    op.state.verify()
    return op


def commit_approval(op: ToolOperation, conn: Any, callback: Callable) -> None:
    """Order the final approval COMMIT with close/timeout, not earlier SQL.

    No arbitrary hooks, provider calls, or filesystem preparation under the
    decision lock. A later close cannot undo an already successful COMMIT.
    """
    if approval_operation(callback) is not op:
        _fail('approval operation changed before commit')
    with op.state.decision_lock:
        op.state.require_active()
        try:
            conn.execute('COMMIT')
        except BaseException:
            op.state.revoke('approval COMMIT failed or acknowledgement lost; outcome unknown')
            raise


def tool_entry(function: Callable) -> Callable:
    """Otocz helper invoke oraz wspólny middleware sekwencyjny/współbieżny."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(agent: Any, *args: Any, **kwargs: Any) -> Any:
        state = check_agent(agent)
        if state is None:
            return function(agent, *args, **kwargs)
        bound = signature.bind(agent, *args, **kwargs).arguments
        name = bound['function_name']
        if name not in state.definitions:
            state.revoke('tool outside protected capability set')
            _fail('tool outside protected capability set: ' + str(name))
        current = _CURRENT.get()
        if current is not None:
            if current.state is not state or current.name != name:
                state.revoke('nested tool changed worker or capability')
                _fail('nested tool changed worker or capability')
            return function(agent, *args, **kwargs)
        metadata = {
            'task_id': bound.get('effective_task_id') or '',
            'session_id': getattr(agent, 'session_id', '') or '',
            'tool_call_id': bound.get('tool_call_id') or '',
            'turn_id': getattr(agent, '_current_turn_id', '') or '',
            'api_request_id': getattr(agent, '_current_api_request_id', '') or '',
        }
        token = _CURRENT.set(ToolOperation(state, name, metadata))
        try:
            result = function(agent, *args, **kwargs)
            check_agent(agent)
            return result
        except BaseException:
            state.revoke('tool execution exited exceptionally; effects may exist')
            raise
        finally:
            _CURRENT.reset(token)
    return wrapped


def suppress_optional_guard_hooks(manager: Any, hook_name: str) -> bool:
    """Guard pracuje obowiązkowo przy handlerze, nie drugi raz jako obserwator."""
    if hook_name not in ('pre_tool_call', 'post_tool_call'):
        return False
    with _MAP_LOCK:
        return manager.scope_key in _PROTECTED_SCOPES


def guarded_registry_dispatch(registry: Any, name: str, args: Any,
                              scope: str | None, kwargs: dict) -> tuple[bool, Any]:
    """Autoryzacja finalnych argumentów i wywołanie przypiętego handlera.

    Kolejność blokad: discovery managera, krótkie odczyty registry, potem
    istniejąca blokada zamiaru/transakcji wewnątrz pluginu. Nie blokujemy
    całego executora, który mógłby oczekiwać własnego wątku roboczego.
    """
    op = _CURRENT.get()
    if op is None:
        effective_scope = scope if scope is not None else registry.current_scope_key()
        with _MAP_LOCK:
            bound_scope = effective_scope in _PROTECTED_SCOPES
        if _protected() or bound_scope:
            _fail('registry call has no mandatory agent operation')
        return False, None
    state = op.state
    try:
        with state.proof.manager._discovery_lock:
            state.verify()
            if (registry is not state.proof.registry or name != op.name
                    or scope not in (None, str(state.proof.home)) or type(args) is not dict
                    or (kwargs.get('task_id') or '') != op.metadata['task_id']):
                _fail('registry operation identity changed')
            with op.lock:
                if op.used:
                    _fail('tool operation replay')
                op.used = True
            frozen = _json(args)
            for callback in (state.pre, *(callback for _, callback in state.extra_pre)):
                observed = json.loads(frozen)
                directive = callback(tool_name=name, args=observed, **op.metadata)
                state.verify()
                if _json(observed) != frozen:
                    _fail('mandatory pre-hook changed arguments without authorization')
                if directive is not None:
                    if (type(directive) is not dict or directive.get('action') != 'block'
                            or not isinstance(directive.get('message'), str) or not directive['message']):
                        _fail('unsupported mandatory guard directive; no interactive approval')
                    return True, json.dumps({'error': directive['message']}, ensure_ascii=False)
            # Funkcja i flagi pochodzą ze snapshotu, nie z kolejnego lookupu
            # podatnego na równoległą podmianę wpisu w rejestrze.
            # Admission is ordered with revocation. Release before calling an
            # arbitrary handler: it may hang. Once admitted, effects are unknown
            # on abandon; this is NOT a promise to kill a Python thread.
            with state.decision_lock:
                state.require_active()
                op.admitted = True
                op.thread_id = threading.get_ident()
            result = registry._dispatch_handler(name, json.loads(frozen),
                                               state.handlers[name], False, kwargs)
            state.verify()
            from model_tools import _tool_result_observer_fields
            status, error_type, error_message = _tool_result_observer_fields(name, result)
            state.post(tool_name=name, args=json.loads(frozen), result=result,
                       status=status, error_type=error_type, error_message=error_message,
                       **op.metadata)
            state.verify()
            return True, result
    except BaseException as exc:
        state.revoke(str(exc) if isinstance(exc, WorkerLifetimeError) else 'mandatory dispatch failed; effects may exist')
        if isinstance(exc, WorkerLifetimeError) or not isinstance(exc, Exception):
            raise
        raise WorkerLifetimeError('FACTORY_WORKER_LIFETIME_REFUSED: mandatory dispatch failed; '
                                  'effects may exist, inspect state, do not retry automatically') from exc
    finally:
        op.finished = True


def check_model_tools(agent: Any, tools: Any = None) -> None:
    """Sprawdź pełny obiekt przed budową, także jawnie przekazaną listę tools."""
    state = check_agent(agent)
    if state is not None:
        state.verify_tools(agent.tools if tools is None else tools)


def record_model_payload(agent: Any, payload: Any, *, expected_tools: Any = None) -> Any:
    """Zachowaj tylko skrót powierzchni narzędzi po natywnej konwersji."""
    state = check_agent(agent)
    if state is not None:
        allowed = None
        if expected_tools is not None:
            state.verify_tools(expected_tools)
            allowed = {t['function']['name'] for t in expected_tools}
        digest = state.envelope(payload, allowed)
        with state.payload_lock:
            if len(state.payloads) >= 256 and digest not in state.payloads:
                state.revoke('model capability cache exhausted')
                _fail('model capability cache exhausted')
            state.payloads.add(digest)
    return payload


def check_model_payload(agent: Any, payload: Any) -> None:
    """Hook/odświeżenie nie może dodać narzędzi po sprawdzonej konwersji."""
    state = check_agent(agent)
    if state is not None:
        with state.payload_lock:
            known = state.envelope(payload) in state.payloads
        if not known:
            state.revoke('model payload capabilities were not built by this worker')
            _fail('model payload capabilities were not built by this worker')


def model_builder(function: Callable) -> Callable:
    """Zwiąż także bezpośrednie wywołanie natywnego buildera poza forwarderem."""
    @functools.wraps(function)
    def wrapped(agent: Any, api_messages: list, tools_for_api: Any = None) -> dict:
        state = check_agent(agent)
        if state is None:
            return function(agent, api_messages, tools_for_api=tools_for_api)
        tools = copy.deepcopy(agent.tools if tools_for_api is None else tools_for_api)
        state.verify_tools(tools)
        result = function(agent, api_messages, tools_for_api=tools)
        return record_model_payload(agent, result, expected_tools=tools)
    return wrapped


def model_request(function: Callable) -> Callable:
    """Sprawdź wejście i powrót; cofnięcie nie odwołuje wysłanego żądania.

    Nie utrzymuj discovery przez sieć ani oczekiwanie na inny wątek.
    Po utracie generacji wynik nie wraca jako gotowa odpowiedź agenta.
    """
    @functools.wraps(function)
    def wrapped(agent: Any, api_kwargs: dict, *args: Any, **kwargs: Any) -> Any:
        state = check_agent(agent)
        if state is None:
            return function(agent, api_kwargs, *args, **kwargs)
        snapshot = copy.deepcopy(api_kwargs)
        check_model_payload(agent, snapshot)
        try:
            return function(agent, snapshot, *args, **kwargs)
        finally:
            check_agent(agent)
    return wrapped


def agent_phase(function: Callable) -> Callable:
    """Wejście pomocnicze także odmawia normalnego wyniku po unieważnieniu."""
    @functools.wraps(function)
    def wrapped(agent: Any, *args: Any, **kwargs: Any) -> Any:
        check_agent(agent)
        try:
            return function(agent, *args, **kwargs)
        finally:
            check_agent(agent)
    return wrapped


def checked_model_call(agent: Any, payload: dict, callback: Callable) -> Any:
    """Końcowy słownik po middleware i sanitizerze, bez blokady nad siecią."""
    state = check_agent(agent)
    if state is None:
        return callback(payload)
    final = copy.deepcopy(payload)
    check_model_payload(agent, final)
    try:
        return callback(final)
    finally:
        check_agent(agent)
