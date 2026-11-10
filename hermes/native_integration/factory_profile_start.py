"""Natywna kontrola STARTU reviewer-gpt/release-manager poza oboma pluginami.

Dowód obu pluginów jest wiązany z agentem po kontroli AFTER. Lifetime sprawdza
jego zachowanie przed dopuszczeniem operacji i zatwierdzeniem approval.
Nie zastępuje cache-safe launchera ani kontroli już importowanego kodu.
"""
from __future__ import annotations
import hashlib
import importlib
import os
import sys
from contextvars import ContextVar
from types import ModuleType
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GUARD = 'factory-execution-guards'
READONLY = 'factory-repository-readonly'
PROFILES = frozenset({'reviewer-gpt', 'release-manager'})
REPO_TOOLS = frozenset({'factory_repo_map','factory_repo_read','factory_repo_search'})
MODEL_TOOLS = {'reviewer-gpt': REPO_TOOLS | {'kanban_show','kanban_request_changes','factory_review_approve'},
               'release-manager': REPO_TOOLS}
DISABLED = frozenset({'terminal','file','code_execution','web','browser','image_gen','delegation','computer_use','cronjob','skills','vision','todo','memory','session_search','clarify','messaging','tts','moa','bfl','x_search','mcp'})
READONLY_PINS = {
    "__init__.py": "45985ac4faa072055a4e03d0ae005bd479fca55a3046de23a399067e7545af71",
    "kanban_guard.py": "f38cb4038757c822eb18ec03faf0b22d8a5ae9f7fb9346410f0433650000b93d",
    "plugin.yaml": "b82696753bc88c1bad7eec5266a3b3d4f37152aa703b7fb850b1c08909b1c337",
    "repo_map.py": "88b7d66c33ce8fb8b80e7e8e6bbf47462abda27246386bc9767ee4d62a74ef3c",
    "repository_tools.py": "a93794aa9aeb8496ce46537a7907d9b66473ea2961efc49f3cac904502d61a80"
}


def selected_profile() -> str | None:
    """Rozpoznaj chronione ścieżki stdlibem, przed natywnymi importami profilu."""
    declared = os.environ.get('HERMES_PROFILE')
    if declared in PROFILES | {'coder-claude'}:
        return declared
    candidates = [os.environ.get('HERMES_HOME', '')]
    # An in-process gateway/task may scope its home with ContextVar rather than
    # environment variables. Read only the existing stdlib ContextVar; do NOT
    # import or call native getters before the selected profile's pin gate.
    # Already imported interpreter/modules remain an explicit trust boundary.
    constants = sys.modules.get('hermes_constants')
    if type(constants) is ModuleType:
        override = vars(constants).get('_HERMES_HOME_OVERRIDE')
        if type(override) is ContextVar:
            scoped = override.get(None)
            if type(scoped) is str and scoped:
                candidates.insert(0, scoped)
    for raw in candidates:
        if raw:
            home = Path(raw).expanduser().resolve(strict=False)
            for profile in PROFILES | {'coder-claude'}:
                parent = 'factory-profiles' if profile=='release-manager' else 'profiles'
                if home == (Path.home()/'.hermes'/parent/profile).resolve(strict=False):
                    return profile
    return None


def _fail(message: str) -> None:
    from factory_worker_start import WorkerStartError
    raise WorkerStartError('FACTORY_PROFILE_START_REFUSED: ' + message)


def _sources(profile: str) -> Path:
    from factory_worker_start import _canonical, _check_sources
    if profile not in PROFILES or os.environ.get('HERMES_PROFILE') != profile:
        _fail('profile identity mismatch')
    raw = os.environ.get('HERMES_HOME','')
    if not raw:
        _fail('profile home missing')
    home = _canonical(Path(raw))
    parent = 'factory-profiles' if profile=='release-manager' else 'profiles'
    if home != Path.home()/'.hermes'/parent/profile:
        _fail('profile home identity mismatch')
    _check_sources(home)  # pełne natywne piny + osiem plików guarda, bez importów Hermesa
    root = _canonical(home/'plugins'/READONLY)
    actual = {}
    for path in root.rglob('*'):
        if path.is_symlink(): _fail('readonly source symlink')
        if path.is_file():
            _canonical(path)
            actual[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != READONLY_PINS:
        _fail('readonly source file set/hash mismatch')
    return home


def _identity(profile: str, home: Path) -> None:
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name
    inferred = 'custom' if profile=='release-manager' else profile
    if os.environ.get('HERMES_PROFILE') != profile or get_hermes_home()!=home or get_active_profile_name()!=inferred:
        _fail('native profile identity mismatch')


def _configuration(profile: str, enabled: Any, disabled: Any) -> None:
    from hermes_cli.config import load_config_readonly
    base = {GUARD, READONLY}
    allowed = [base | {'kanban'}] if profile=='reviewer-gpt' else [base,base | {'kanban'}]
    if (type(enabled) is not list or any(type(x) is not str for x in enabled)
            or len(enabled)!=len(set(enabled)) or set(enabled) not in allowed
            or type(disabled) is not list or any(type(x) is not str for x in disabled)
            or len(disabled)!=len(DISABLED) or set(disabled)!=DISABLED):
        _fail('explicit toolsets/disabled boundary mismatch')
    config = load_config_readonly()
    plugins = config.get('plugins', {})
    entries = {GUARD:{'allow_tool_override':True},READONLY:{'allow_tool_override':False}}
    if (plugins.get('enabled') != [GUARD,READONLY] or plugins.get('disabled',[])!=[]
            or plugins.get('entries')!=entries
            or plugins['entries'][GUARD]['allow_tool_override'] is not True
            or plugins['entries'][READONLY]['allow_tool_override'] is not False):
        _fail('plugin configuration mismatch')
    stored = config.get('agent',{}).get('disabled_toolsets')
    if type(stored) is not list or any(type(x) is not str for x in stored) or len(stored)!=len(DISABLED) or set(stored)!=DISABLED:
        _fail('stored disabled boundary mismatch')
    if config.get('mcp_servers',{})!={} or config.get('tools',{}).get('tool_search',{}).get('enabled')!='off':
        _fail('dynamic capability discovery must be disabled')
    backend = 'native-openai' if profile=='reviewer-gpt' else 'read-only-release-decision'
    if config.get('factory',{}).get('execution_backend') != backend:
        _fail('profile backend configuration mismatch')


@dataclass(frozen=True)
class ProfileStartProof:
    profile: str
    home: Path
    manager: Any
    loaded: tuple
    entries: dict
    metadata: dict
    enabled: tuple
    disabled: tuple
    pid: int
    registry: Any
    module: Any
    readonly_module: Any
    bindings: tuple

    def verify(self) -> None:
        from factory_worker_start import _module_source, _entry_metadata
        from hermes_cli.plugins import get_plugin_manager
        from tools.registry import registry
        if (self.pid != os.getpid() or _sources(self.profile)!=self.home
                or tuple((k, os.environ.get(k)) for k, _ in self.bindings) != self.bindings):
            _fail('profile/source identity changed')
        _identity(self.profile,self.home)
        _configuration(self.profile,list(self.enabled),list(self.disabled))
        if (get_plugin_manager() is not self.manager or registry is not self.registry
                or registry.current_scope_key()!=str(self.home) or self.manager.scope_key!=str(self.home)):
            _fail('manager/registry scope changed')
        for name, loaded in zip((GUARD,READONLY),self.loaded):
            if self.manager._plugins.get(name) is not loaded or not loaded.enabled or loaded.error or loaded.module is None:
                _fail('required plugin unloaded/replaced')
            _module_source(loaded.module,self.home/'plugins'/name/'__init__.py')
        guard,readonly = (x.module for x in self.loaded)
        if guard is not self.module or readonly is not self.readonly_module:
            _fail('required module generation changed')
        expected = {'pre_tool_call':[guard.on_pre_tool_call,readonly.on_pre_tool_call],
                    'post_tool_call':[guard.on_post_tool_call]}
        for name,callbacks in expected.items():
            actual=self.manager._hooks.get(name,[])
            if len(actual)!=len(callbacks) or set(actual)!=set(callbacks):
                _fail('required hook identity/count mismatch')
        for name,entry in self.entries.items():
            if registry.get_entry(name) is not entry or _entry_metadata(entry)!=self.metadata[name]:
                _fail('registered entry changed: '+name)
        handlers = {'factory_repo_map':readonly.handle_map,'factory_repo_read':readonly.handle_read,
                    'factory_repo_search':readonly.handle_search}
        if self.profile=='reviewer-gpt':
            from tools import kanban_tools
            handlers.update(factory_review_approve=guard._handle_review_approve,
                            kanban_show=kanban_tools._handle_show,
                            kanban_request_changes=kanban_tools._handle_request_changes)
        for name,handler in handlers.items():
            if self.entries[name].handler is not handler:
                _fail('approved handler identity mismatch: '+name)
        for entry in registry.get_all_entries():
            if entry.toolset=='kanban' and entry.name not in MODEL_TOOLS[self.profile]:
                if entry.handler is not guard._disabled_kanban_handler or entry.check_fn is not guard._never_available:
                    _fail('scoped Kanban restriction absent: '+entry.name)


def before_profile_init(profile: str, enabled: Any, disabled: Any, *, provider: Any = None) -> ProfileStartProof:
    from factory_worker_start import WorkerStartError, _entry_metadata
    try:
        home=_sources(profile)
        from factory_model_boundary import is_moa_provider
        if is_moa_provider(provider):
            _fail('MoA route is unsupported for the protected profile')
        _identity(profile,home)
        _configuration(profile,enabled,disabled)
        # Prywatna tablica zadania: hook one-start przed pierwszym narzędziem.
        from factory_one_start import install_for_worker
        install_for_worker()
        from tools import kanban_tools  # noqa: F401
        from hermes_cli.plugins import discover_plugins,get_plugin_manager
        from tools.registry import registry
        discover_plugins()
        manager=get_plugin_manager()
        loaded=tuple(manager._plugins.get(n) for n in (GUARD,READONLY))
        if any(x is None or not x.enabled or x.error or x.module is None for x in loaded):
            _fail('required plugins unavailable')
        entries={n:registry.get_entry(n) for n in MODEL_TOOLS[profile]}
        if any(e is None for e in entries.values()): _fail('required tool unavailable')
        binding_names = ('HOME', 'HERMES_HOME', 'HERMES_PROFILE', 'HERMES_KANBAN_TASK',
                         'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_RUN_ID', 'HERMES_KANBAN_WORKSPACE',
                         'HERMES_KANBAN_HOME', 'HERMES_KANBAN_CLAIM_LOCK', 'FACTORY_RELEASE_TASK', 'FACTORY_RELEASE_WORKSPACE')
        proof=ProfileStartProof(profile,home,manager,loaded,entries,
             {n:_entry_metadata(e) for n,e in entries.items()},tuple(enabled),tuple(disabled),os.getpid(),
             registry,loaded[0].module,loaded[1].module,
             tuple((k,os.environ.get(k)) for k in binding_names))
        proof.verify()
        return proof
    except WorkerStartError:
        raise
    except Exception as exc:
        raise WorkerStartError('FACTORY_PROFILE_START_REFUSED: '+type(exc).__name__) from exc


def after_profile_init(proof: ProfileStartProof, agent: Any) -> None:
    """Odmów nowej/utraconej definicji; nie przycinaj nadmiarowej powierzchni."""
    from factory_worker_start import WorkerStartError, _json
    try:
        from tools.registry import registry
        from tools.schema_sanitizer import sanitize_tool_schemas
        proof.verify()
        from factory_model_boundary import is_moa_provider
        if is_moa_provider(getattr(agent, 'provider', None)):
            _fail('MoA route is unsupported for the protected profile')
        expected=MODEL_TOOLS[proof.profile]
        if type(agent.tools) is not list:
            _fail('invalid model definitions')
        names=[t['function']['name'] for t in agent.tools]
        if len(names)!=len(expected) or set(names)!=expected or agent.valid_tool_names!=expected:
            _fail('profile model capability surface mismatch')
        reference=sanitize_tool_schemas(registry.get_definitions(set(expected),quiet=True))
        index={t['function']['name']:_json(t) for t in reference}
        if set(index)!=expected or any(index[t['function']['name']]!=_json(t) for t in agent.tools):
            _fail('profile model schema mismatch')
        proof.verify()
    except WorkerStartError:
        raise
    except Exception as exc:
        raise WorkerStartError('FACTORY_PROFILE_START_REFUSED: '+type(exc).__name__) from exc
