from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).parent / "plugins" / "factory-execution-guards"
SPEC = importlib.util.spec_from_file_location(
    "factory_execution_guards_targeted_review_test",
    PACKAGE_DIR / "__init__.py",
    submodule_search_locations=[str(PACKAGE_DIR)],
)
assert SPEC and SPEC.loader
PLUGIN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLUGIN
SPEC.loader.exec_module(PLUGIN)


class TargetedReviewGuardTests(unittest.TestCase):
    def test_runtime_guard_allows_only_exact_targeted_review_argv(self):
        wrapper = "/runtime/kanban_runtime_cli.sh"
        with patch.object(PLUGIN._guard, "_runtime_wrapper_paths", return_value={wrapper}):
            self.assertTrue(
                PLUGIN._runtime_terminal_allowed(
                    f"{wrapper} dispatch-review --board isolated --task-id t_review"
                )
            )
            for command in (
                f"{wrapper} dispatch-review",
                f"{wrapper} dispatch-review --task-id",
                f"{wrapper} dispatch-review --task-id --evil",
                f"{wrapper} dispatch-review --task-id t_review extra",
                f"{wrapper} dispatch-review --actual-json '{{}}'",
                f"{wrapper} dispatch-review t_review",
            ):
                with self.subTest(command=command):
                    self.assertFalse(PLUGIN._runtime_terminal_allowed(command))


if __name__ == "__main__":
    unittest.main()
