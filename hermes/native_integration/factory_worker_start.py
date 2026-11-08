"""Obowiązkowa granica startu coder-claude, wykonywana poza ładowanym pluginem.

Kandydat integracji z przypiętym run_agent.py; nie jest samodzielnym instalatorem.
Nie nadaje uprawnień recenzji. Na tablicy prywatnego zadania (sf-job-*) instaluje
hook one-start (factory_one_start) i sprawdza go jednym połączeniem; poza nią
bazy zadań nie otwiera.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

PROFILE = "coder-claude"
PLUGIN = "factory-execution-guards"
TOOLSETS = frozenset({"terminal", "file", "kanban", PLUGIN})
MODEL_TOOLS = frozenset({
    "terminal", "read_file", "search_files", "kanban_show", "kanban_comment",
    "kanban_heartbeat", "kanban_request_review", "kanban_block",
})
# Nie przyjmujemy nieznanego rozszerzenia katalogu po zmianie wersji Hermesa.
RAW_TOOLS = MODEL_TOOLS | frozenset({
    "process", "patch", "write_file", "kanban_complete", "kanban_create",
    "kanban_link", "kanban_request_changes", "kanban_attach", "kanban_attach_url",
    "kanban_attachments",
})
# Ten plik jest generowany razem z lokalnym kandydatem, nie z mutable profile config.
SOURCE_PINS: dict[str, dict[str, str]] = {
    "plugin": {
        "__init__.py": "c595c6b4f21bbec8b01289a93c4a3e6f12b270a7c28fc950f47f4ca1bf51f5a2",
        "guard.py": "20f3631f01d5fb0e2f933e9569a49253f7748b9092e4252d69a2ece127060197",
        "handoff.py": "b0d65969f0c502139fe4d8c3fbec9121d73caedd39ed5805679ef9074a65ea93",
        "plugin.yaml": "49525d9203b2793ddf920932304ae56524ea8dddf0943e919436864de5bd16eb",
        "review_intent.py": "c53e8bb17c7e2ace531b52f14311ae4000ef5d04db3cc529c0bd65bda47a9287",
        "review_registration.py": "3edb0fc832a1928a5f882e19c3be489723bf5fc2a901b993c99a0e4bee2b568a",
        "review_transaction.py": "9749e781670f9ff9aa9423d193da1be15f132dac4c6d74d3b531b7850800276e",
        "supervisor.py": "3c3de6531fc9007cad95dc734eddcde1f7cf6cd6b202fce0a03bdb02469928ed"
    },
    "native": {
        "factory_one_start.py": "87b450e97bde40c5b2333d65b1117a1427ab7c986d7e2b3edaf7b016c63a9062",
        "factory_finalization_core.py": "971b733b925f59fbef76e4d618d11cf4d1509f976b778f1797cb54d31a830533",
        "run_agent.py": "945f36b2ca37b13bc423acd297910de473b123ca2015bed585273ec28fe2c6c9",
        "tools/registry.py": "9c27d7a0bb5e73cfdff9caacaf3b2aa3dfd62804adf2b1ae365553931f50a985",
        "hermes_cli/plugins.py": "a1ae9a9d92fe9d713b8d6fde6d44d2ce4828cea99a90265d569a5b286884c383",
        "hermes_cli/profiles.py": "db1b0ae8657d1731597d33e63502482246b0fd7d7df37b5af13f2d13a65e5ec0",
        "hermes_constants.py": "58b8d5f17bc6d23dc192990bb71cb92725ed37b6c99a4a5b4cc97ac807846d56",
        "registration_lifecycle.py": "42c7a95159a02cd5dd252a5f5d43e81d083656b3498b4b95bc09b078e3fbdee0",
        "model_tools.py": "32a106d66835dc9f88f15624076086a53cbd4bb7ed80889228d0e53f62d4cfac",
        "tools/schema_sanitizer.py": "62a584bc68ecfd05df1c199980007bcc687fea0e549c18542b9d82de419f329e",
        "tools/terminal_tool.py": "59a0f637caec97c0a87aed5c667267c537d8747dcab91b408850e34c9eeba64a",
        "tools/file_tools.py": "18b40343d3fd890692762e8a434fa7e2e96b953766108a797b4af2caef01dd19",
        "tools/kanban_tools.py": "6066ca6acdbdc6b48258a2a31df0e4942eca883f076c9f56a10fe78370566260",
        "hermes_cli/kanban_db.py": "1f825422dd7d6cb36bca65676d2225ac55abc6dbb832b9ba0f4aa25b00d5f022",
        "hermes_cli/sqlite_util.py": "55f41975ed3af6b15c3ca2e2a9dc782b7b99477dd36bfe48d11f462743e56438",
        "agent/agent_init.py": "0c51a03ef3f0bbc2b3400dbb21568fc05ad5dde305cf7ead5771f01999f5e983",
        "factory_worker_lifetime.py": "825b631914a0c1a38ae735f3b69ccdc079693977e051f1634e6f55a2c1dc0b9a",
        "agent/agent_runtime_helpers.py": "6ed3d3fb3666a481f6dea2fde47abb1c93c16d5ab329746fef6e44f27cd0399a",
        "agent/tool_executor.py": "8bfc17d18e8cd14dcfe20cbb2c699279e902fb1111f40465ab2889c2f8763ca8",
        "agent/chat_completion_helpers.py": "3e51828008bf8a74d489597438558c8e75952247baa79dbf42d584df1e91bd16",
        "agent/conversation_loop.py": "b7fc051f8c1fa165d52b33e1946fb39f1b4c289e0f1da11f38ca3e8c82cc54b7",
        "hermes_cli/lifecycle.py": "e820ed8114d7062d53223113cc053c737d4ced6b624ce1fbe88f79ad8b2a26bd",
        "hermes_cli/config.py": "b4846228f7767d0fc3b4793da2e96d5d12c27089695574b0d7acf18624e49920",
        "factory_model_boundary.py": "cdfa6fdf59293126416e172b8b224e9b76a2b376c18c8df8e35dae5efcbf3b25",
        "agent/codex_runtime.py": "863041b47f046af78c76bab6bee9e89113ab1e114a4e6ab98b484a13b7f2926f",
        "agent/anthropic_adapter.py": "10a47719f8b54f4fe3bd81eebba3ddc70d08106745148a5bea1992c2d8f6462d",
        "factory_profile_start.py": "70b792781c5d36404d31141a2992af98926ba64c76789b1eb9269a460ee56fc3"
    }
}
_HANDLERS = {
    "terminal": ("tools.terminal_tool", "_handle_terminal", "check_terminal_requirements"),
    "read_file": ("tools.file_tools", "_handle_read_file", "_check_file_reqs"),
    "search_files": ("tools.file_tools", "_handle_search_files", "_check_file_reqs"),
    "kanban_show": ("tools.kanban_tools", "_handle_show", "_check_kanban_mode"),
    "kanban_comment": ("tools.kanban_tools", "_handle_comment", "_check_kanban_mode"),
    "kanban_heartbeat": ("tools.kanban_tools", "_handle_heartbeat", "_check_kanban_mode"),
    "kanban_block": ("tools.kanban_tools", "_handle_block", "_check_kanban_mode"),
}


class WorkerStartError(RuntimeError):
    """Brak dowodu gotowości zamyka start, zamiast pozostawiać niechroniony agent."""


def _fail(message: str) -> None:
    raise WorkerStartError("FACTORY_WORKER_START_REFUSED: " + message)


def _canonical(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not path.is_absolute() or str(path) != str(resolved):
        _fail("noncanonical source/profile path")
    current = path
    while current != current.parent:
        if current.is_symlink():
            _fail("source/profile symlink")
        current = current.parent
    return path


_BYTECODE_PREFIX: tuple[int, int, str] | None = None


def _check_bytecode_cache() -> None:
    # G3: -B/PYTHONDONTWRITEBYTECODE zabrania zapisu, nie odczytu. Wymagamy prywatnego, pustego prefiksu cache
    # utworzonego dla tego uruchomienia: CPython czyta wtedy .pyc wyłącznie spod prefiksu, więc żaden istniejący
    # .pyc (drzewo runtime, site-packages, stdlib) nie jest wykonywany — każdy moduł kompiluje się ze źródła.
    # Brak prefiksu, prefiks odziedziczony/współdzielony, niepusty lub nieprywatny = odmowa.
    if not sys.dont_write_bytecode:
        _fail("bytecode writes enabled")
    prefix = sys.pycache_prefix
    if prefix is None or os.environ.get("PYTHONPYCACHEPREFIX") != prefix:
        _fail("private bytecode cache prefix required")
    path = Path(prefix)
    try:
        st = path.lstat()
    except OSError:
        _fail("private bytecode cache prefix missing")
    if (not path.is_absolute() or stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700):
        _fail("bytecode cache prefix not private")
    global _BYTECODE_PREFIX
    identity = (st.st_dev, st.st_ino, str(path))
    if _BYTECODE_PREFIX is None:
        # Pierwsze sprawdzenie (start procesu, przed kodem narzędzi): prefiks musi być pusty.
        if any(path.iterdir()):
            _fail("bytecode cache prefix not empty")
        _BYTECODE_PREFIX = identity
    elif _BYTECODE_PREFIX != identity:
        # Później: ten sam prywatny katalog. Zawartość może pochodzić wyłącznie z kompilacji tego procesu
        # ze źródeł (np. natywny linter py_compile ignoruje PYTHONDONTWRITEBYTECODE).
        _fail("bytecode cache prefix changed")


def _check_sources(home: Path) -> None:
    _check_bytecode_cache()
    plugin_root = _canonical(home / "plugins" / PLUGIN)
    actual = set()
    for path in plugin_root.rglob("*"):
        if path.is_symlink():
            _fail("plugin source symlink")
        if path.is_file():
            relative = path.relative_to(plugin_root).as_posix()
            # Cache wewnętrzny jest niedozwolonym dodatkowym plikiem pluginu.
            actual.add(relative)
    if actual != set(SOURCE_PINS["plugin"]):
        _fail("plugin file set changed")
    native_root = _canonical(Path(__file__).resolve().parent)
    for root, pins in ((plugin_root, SOURCE_PINS["plugin"]),
                       (native_root, SOURCE_PINS["native"])):
        for name, expected in pins.items():
            path = _canonical(root / name)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                _fail("source pin mismatch: " + name)


def _source_home() -> Path:
    """Ustal ścieżkę przed importem któregokolwiek z kontrolowanych modułów."""
    if os.environ.get("HERMES_PROFILE") != PROFILE:
        _fail("profile identity mismatch")
    raw = os.environ.get("HERMES_HOME", "")
    if not raw:
        _fail("profile home missing")
    home = _canonical(Path(raw))
    if home != Path.home() / ".hermes" / "profiles" / PROFILE:
        _fail("profile identity mismatch: profile home mismatch")
    return home


def _identity() -> Path:
    from hermes_cli.profiles import get_active_profile_name
    from hermes_constants import get_hermes_home
    if get_active_profile_name() != PROFILE or os.environ.get("HERMES_PROFILE") != PROFILE:
        _fail("profile identity mismatch")
    home = _canonical(Path(os.environ["HERMES_HOME"]))
    if home != get_hermes_home() or home != Path.home() / ".hermes" / "profiles" / PROFILE:
        _fail("profile identity mismatch: profile home mismatch")
    return home


def _configuration(enabled: Any, disabled: Any) -> None:
    from hermes_cli.config import load_config_readonly
    if (type(enabled) is not list or any(type(x) is not str for x in enabled)
            or len(enabled) != len(TOOLSETS) or set(enabled) != TOOLSETS
            or disabled not in (None, [])):
        _fail("explicit worker toolset selection mismatch")
    config = load_config_readonly()
    if not isinstance(config, dict):
        _fail("profile config unavailable")
    plugins = config.get("plugins", {})
    if (plugins.get("enabled") != [PLUGIN] or plugins.get("disabled", []) != []
            or plugins.get("entries") != {PLUGIN: {"allow_tool_override": True}}
            or plugins["entries"][PLUGIN].get("allow_tool_override") is not True):
        _fail("plugin configuration mismatch")
    if (config.get("mcp_servers", {}) != {}
            or config.get("tools", {}).get("tool_search", {}).get("enabled") != "off"):
        _fail("dynamic capability discovery must be disabled")
    if (config.get("factory", {}).get("execution_backend") != "claude-code"
            or config.get("factory", {}).get("claude_model_class") != "sonnet"):
        _fail("coder backend configuration mismatch")


def _module_source(module: ModuleType, expected: Path) -> None:
    if (not isinstance(module, ModuleType)
            or Path(getattr(module, "__file__", "")) != expected
            or getattr(module, "__spec__", None) is None
            or Path(module.__spec__.origin or "") != expected):
        _fail("loaded module origin mismatch")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class WorkerStartProof:
    """Krótkotrwały uchwyt TEGO procesu, nie deklaracja w środowisku ani pliku."""
    home: Path
    manager: Any
    loaded: Any
    module: ModuleType
    registry: Any
    native_entries: dict[str, Any]
    entry_metadata: dict[str, str]
    enabled: tuple[str, ...]
    disabled: tuple[str, ...]
    pid: int

    def verify(self) -> None:
        """Ponownie sprawdź moduł, hooki, ledger oraz rzeczywiste aktywne wpisy."""
        from hermes_cli.plugins import get_plugin_manager
        from tools.registry import registry
        if (self.pid != os.getpid() or _identity() != self.home
                or get_plugin_manager() is not self.manager or registry is not self.registry
                or registry.current_scope_key() != str(self.home)
                or self.manager.scope_key != str(self.home)):
            _fail("worker/profile/registry identity changed")
        _check_sources(self.home)
        _configuration(list(self.enabled), list(self.disabled))
        loaded = self.manager._plugins.get(PLUGIN)
        if (loaded is not self.loaded or not loaded.enabled or loaded.error is not None
                or loaded.module is not self.module):
            _fail("plugin unavailable or replaced")
        _module_source(self.module, self.home / "plugins" / PLUGIN / "__init__.py")
        for hook_name, function in (("pre_tool_call", self.module.on_pre_tool_call),
                                    ("post_tool_call", self.module.on_post_tool_call)):
            if self.manager._hooks.get(hook_name) != [function]:
                _fail("required hook identity/count changed: " + hook_name)
            owned = [r for r in self.manager._ownership_ledger.get(PLUGIN, [])
                     if r.kind == "hook" and r.key == hook_name and r.active]
            if len(owned) != 1:
                _fail("hook lifecycle ownership changed")
        registration = importlib.import_module(self.module.__name__ + ".review_registration")
        registration.verify_active_registration()
        for name, entry in self.native_entries.items():
            if (registry.snapshot_registration(name) is not entry
                    or registry.snapshot_registration(name, scope=str(self.home)) is not None
                    or registry.get_entry(name) is not entry
                    or _entry_metadata(entry) != self.entry_metadata[name]):
                _fail("native tool identity changed: " + name)
            module_name, handler_name, check_name = _HANDLERS[name]
            native = importlib.import_module(module_name)
            _module_source(native, Path(__file__).parent / (module_name.replace(".", "/") + ".py"))
            if entry.handler is not getattr(native, handler_name) or entry.check_fn is not getattr(native, check_name):
                _fail("native handler/check identity changed: " + name)


def _entry_metadata(entry: Any) -> str:
    return _json({k: getattr(entry, k) for k in (
        "name", "toolset", "schema", "requires_env", "is_async", "description", "emoji",
        "max_result_size_chars", "dynamic_schema_overrides")})


def before_agent_init(enabled: Any, disabled: Any, *, provider: Any = None) -> WorkerStartProof:
    """Odmów PRZED konstruktorem providerów także wtedy, gdy plugin nie istnieje."""
    try:
        home = _source_home()
        _check_sources(home)
        from factory_model_boundary import is_moa_provider
        if is_moa_provider(provider):
            _fail("MoA is unsupported for coder-claude; no advisor/facade work is authorized")
        if _identity() != home:
            _fail("profile identity changed during source verification")
        _configuration(enabled, disabled)
        # Prywatna tablica zadania: hook one-start przed pierwszym narzędziem.
        from factory_one_start import install_for_worker
        install_for_worker()
        # Jawny import bazowych tools poprzedza discovery. Loader może pominąć
        # plugin z błędem; wynik weryfikujemy niezależnie, nie ufamy brakowi wyjątku.
        from tools import terminal_tool, file_tools, kanban_tools  # noqa: F401
        from hermes_cli.plugins import discover_plugins, get_plugin_manager
        from tools.registry import registry
        discover_plugins()
        manager = get_plugin_manager()
        loaded = manager._plugins.get(PLUGIN)
        if loaded is None or loaded.module is None or not loaded.enabled or loaded.error:
            _fail("mandatory execution guard did not load")
        entries = {name: registry.snapshot_registration(name) for name in _HANDLERS}
        if any(entry is None for entry in entries.values()):
            _fail("required native tool missing")
        proof = WorkerStartProof(home, manager, loaded, loaded.module, registry, entries,
                                 {n: _entry_metadata(e) for n, e in entries.items()},
                                 tuple(enabled), tuple(disabled or ()), os.getpid())
        proof.verify()
        return proof
    except WorkerStartError:
        raise
    except Exception as exc:
        raise WorkerStartError("FACTORY_WORKER_START_REFUSED: " + type(exc).__name__) from exc


def after_agent_init(proof: WorkerStartProof, agent: Any) -> None:
    """Zbuduj modelowi dokładnie osiem dozwolonych narzędzi z realnego katalogu.

    To jawne zawężenie uprawnień, nie ponowne nadpisywanie natywnego rejestru.
    Każda nowa/utracona nazwa w surowym katalogu zatrzymuje start do recenzji.
    """
    try:
        proof.verify()
        from factory_model_boundary import is_moa_provider
        if is_moa_provider(getattr(agent, "provider", None)):
            _fail("MoA resolved during initialization; protected worker cannot be bound")
        tools = agent.tools
        if type(tools) is not list:
            _fail("model definitions are not a list")
        names = [item["function"]["name"] for item in tools]
        if len(names) != len(set(names)) or set(names) != RAW_TOOLS:
            _fail("raw native capability surface changed")
        if agent.valid_tool_names != RAW_TOOLS:
            _fail("agent names/definitions disagree")
        from tools.schema_sanitizer import sanitize_tool_schemas
        reference = sanitize_tool_schemas(proof.registry.get_definitions(set(RAW_TOOLS), quiet=True))
        index = {item["function"]["name"]: item for item in reference}
        if set(index) != RAW_TOOLS or any(_json(item) != _json(index[item["function"]["name"]]) for item in tools):
            _fail("model/native schema mismatch")
        selected = [copy.deepcopy(item) for item in tools if item["function"]["name"] in MODEL_TOOLS]
        proof.verify()
        agent.tools = selected
        agent.valid_tool_names = set(MODEL_TOOLS)
    except WorkerStartError:
        raise
    except Exception as exc:
        raise WorkerStartError("FACTORY_WORKER_START_REFUSED: " + type(exc).__name__) from exc
