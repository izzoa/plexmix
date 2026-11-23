"""The process rule that keeps local models from crashing PlexMix (design D1, D9).

One rule, two halves:

* The web server process and the CLI process never load the local model runtime
  (torch, scikit-learn, scipy, transformers, sentence-transformers). Code that needs a
  model runs it in a worker process: ``_local_embedding_worker`` and ``_local_llm_worker``.
* A local-model worker process never loads FAISS. The vector index loads it on first use,
  inside ``_faiss`` in ``src/plexmix/database/vector_index.py``.

Why: FAISS's copy of the OpenMP threading library next to torch's copy in one process
crashes on macOS (a segmentation fault, or an ``OMP: Error #15`` abort, depending on which
library loaded first). Every offending import sits inside a function, so importing the
modules never runs it, and CI installs no torch. The rule is therefore a static one: this
module reads PlexMix's own source with ``ast`` and needs no local model library.

What it covers: PlexMix's own ``import`` statements. A string-based import, or a third-party
package that imports torch itself, is not caught.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PLEXMIX = REPO_ROOT / "src" / "plexmix"
PLEXMIX_UI = REPO_ROOT / "plexmix_ui"
TESTS_DIR = REPO_ROOT / "tests"

# Imported only in a local-model worker process.
LOCAL_MODEL_RUNTIME = frozenset(
    {"torch", "sentence_transformers", "transformers", "sklearn", "scipy"}
)
FAISS = "faiss"

# File suffix -> the module-level functions that run only in a spawned worker process.
WORKER_FUNCTIONS: Dict[str, FrozenSet[str]] = {
    "plexmix/utils/embeddings.py": frozenset({"_local_embedding_worker"}),
    "plexmix/ai/local_provider.py": frozenset({"_local_llm_worker"}),
}
# File suffix -> the module-level function that loads FAISS on first use.
FAISS_FUNCTIONS: Dict[str, FrozenSet[str]] = {
    "plexmix/database/vector_index.py": frozenset({"_faiss"}),
}

# The allowed sites, and what each one must still import (the liveness check).
ALLOWED_SITES: List[Tuple[str, str, FrozenSet[str]]] = [
    (
        "plexmix/utils/embeddings.py",
        "_local_embedding_worker",
        frozenset({"sentence_transformers"}),
    ),
    ("plexmix/ai/local_provider.py", "_local_llm_worker", frozenset({"torch", "transformers"})),
    ("plexmix/database/vector_index.py", "_faiss", frozenset({"faiss"})),
]

# Looked for in ``sys.modules`` of a child interpreter that only imports PlexMix.
WATCHED_MODULES = ("faiss", "torch", "sklearn", "scipy", "transformers", "sentence_transformers")


def _imported_names(node: ast.AST) -> List[str]:
    """The top-level package names that an import statement loads (none for other nodes)."""
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module.split(".")[0]]
    return []


def _outermost_function(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> Optional[ast.AST]:
    """The outermost function that holds ``node``, or None at module or class level.

    "Inside" means the outermost enclosing function, so a nested helper cannot hide an import.
    """
    outer: Optional[ast.AST] = None
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            outer = current
    return outer


def _parent_map(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    parents: Dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def scan_source(source: str, path: str, *, allow_sites: bool = True) -> List[str]:
    """The import-rule violations in one file.

    ``path`` is the file's path with forward slashes: the allowed sites are recognised by
    their ending (``plexmix/utils/embeddings.py``). A violation reads
    ``<path>:<line>: import of <package> in <function or <module>>``.

    With ``allow_sites=False`` no site is allowed (the rule for ``tests/``).
    """
    tree = ast.parse(source, filename=path)
    parents = _parent_map(tree)
    found: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name != FAISS and name not in LOCAL_MODEL_RUNTIME:
                continue
            outer = _outermost_function(node, parents)
            # An allowed function is a module-level function: a method that happens to
            # carry the name is not the worker.
            at_module_level = outer is not None and isinstance(parents.get(outer), ast.Module)
            outer_name = getattr(outer, "name", None)
            table = FAISS_FUNCTIONS if name == FAISS else WORKER_FUNCTIONS
            allowed = (
                allow_sites
                and at_module_level
                and any(
                    f"/{path}".endswith(f"/{suffix}") and outer_name in functions
                    for suffix, functions in table.items()
                )
            )
            if not allowed:
                where = outer_name if outer_name else "<module>"
                found.append((node.lineno, f"{path}:{node.lineno}: import of {name} in {where}"))
    return [text for _, text in sorted(found)]


def _scan_folder(folder: Path, *, allow_sites: bool = True) -> List[str]:
    """Violations in every ``.py`` file under ``folder``, with paths relative to the repo."""
    violations: List[str] = []
    for file in sorted(folder.rglob("*.py")):
        relative = file.relative_to(REPO_ROOT).as_posix()
        violations.extend(
            scan_source(file.read_text(encoding="utf-8"), relative, allow_sites=allow_sites)
        )
    return violations


def _runtime_violations(violations: List[str]) -> List[str]:
    return [v for v in violations if f"import of {FAISS} " not in v]


def _faiss_violations(violations: List[str]) -> List[str]:
    return [v for v in violations if f"import of {FAISS} " in v]


# ---------------------------------------------------------------------------
# The scanner itself (small source strings, no files)
# ---------------------------------------------------------------------------


def test_scanner_flags_a_function_level_import() -> None:
    source = (
        "def build():\n"
        "    from sentence_transformers import SentenceTransformer\n"
        "    return SentenceTransformer\n"
        "\n"
        "def search():\n"
        "    import faiss\n"
        "    return faiss\n"
        "\n"
        "import torch\n"
        "\n"
        "class Provider:\n"
        "    def load(self):\n"
        "        import sklearn.metrics\n"
        "        import transformers.models as models\n"
        "        from scipy import sparse\n"
        "\n"
        "def outer():\n"
        "    def nested():\n"
        "        import torch\n"
        "    return nested\n"
    )

    found = scan_source(source, "src/plexmix/example.py")

    assert found == [
        "src/plexmix/example.py:2: import of sentence_transformers in build",
        "src/plexmix/example.py:6: import of faiss in search",
        "src/plexmix/example.py:9: import of torch in <module>",
        "src/plexmix/example.py:13: import of sklearn in load",
        "src/plexmix/example.py:14: import of transformers in load",
        "src/plexmix/example.py:15: import of scipy in load",
        "src/plexmix/example.py:19: import of torch in outer",
    ]
    # An import of an unrelated package, or a relative import, is not an offence.
    assert scan_source("import numpy\nfrom . import faiss\nfrom .torch import x\n", "a.py") == []


def test_scanner_allows_the_worker_functions() -> None:
    embeddings = (
        "def _local_embedding_worker(model_name, trust_remote_code, device, conn):\n"
        "    try:\n"
        "        from sentence_transformers import SentenceTransformer\n"
        "        def helper():\n"
        "            import torch\n"
        "    except ImportError:\n"
        "        pass\n"
    )
    llm = (
        "def _local_llm_worker(model_name, trust_remote_code, torch_dtype, device, conn):\n"
        "    import torch\n"
        "    from transformers import AutoModelForCausalLM\n"
    )
    index = "def _faiss():\n    import faiss\n    return faiss\n"

    assert scan_source(embeddings, "src/plexmix/utils/embeddings.py") == []
    assert scan_source(llm, "src/plexmix/ai/local_provider.py") == []
    assert scan_source(index, "src/plexmix/database/vector_index.py") == []

    # The allowance is for that function in that file only.
    assert scan_source(embeddings, "src/plexmix/utils/other.py") != []
    assert scan_source(llm, "src/plexmix/utils/embeddings.py") != []
    assert scan_source(index, "src/plexmix/ai/local_provider.py") != []
    # FAISS is not allowed in a model worker, and a model runtime is not allowed in _faiss.
    assert scan_source(
        "def _local_embedding_worker():\n    import faiss\n", "plexmix/utils/embeddings.py"
    )
    assert scan_source("def _faiss():\n    import torch\n", "plexmix/database/vector_index.py")
    # A method with the worker's name is not the worker, and nothing is allowed in tests/.
    method = "class A:\n    def _faiss(self):\n        import faiss\n"
    assert scan_source(method, "src/plexmix/database/vector_index.py") != []
    assert scan_source(index, "src/plexmix/database/vector_index.py", allow_sites=False) != []


# ---------------------------------------------------------------------------
# PlexMix's own source
# ---------------------------------------------------------------------------


def _own_source_violations() -> List[str]:
    return _scan_folder(SRC_PLEXMIX) + _scan_folder(PLEXMIX_UI)


def test_no_local_model_runtime_outside_workers() -> None:
    assert SRC_PLEXMIX.is_dir() and PLEXMIX_UI.is_dir()

    found = _runtime_violations(_own_source_violations())

    assert found == [], (
        "torch, sentence_transformers, transformers, sklearn and scipy may be imported only "
        "inside _local_embedding_worker (utils/embeddings.py) and _local_llm_worker "
        "(ai/local_provider.py). Found:\n" + "\n".join(found)
    )


def test_faiss_only_in_its_loader() -> None:
    found = _faiss_violations(_own_source_violations())

    assert found == [], (
        "faiss may be imported only inside _faiss() in database/vector_index.py, so that a "
        "local-model worker never loads it. Found:\n" + "\n".join(found)
    )


def test_tests_never_import_the_local_model_runtime() -> None:
    """The pytest process must not load torch next to the FAISS tests (design D10)."""
    found = _runtime_violations(_scan_folder(TESTS_DIR, allow_sites=False))

    assert found == [], (
        "A test module must not import torch, sentence_transformers, transformers, sklearn "
        "or scipy. Decide with importlib.util.find_spec, or use a fake worker. Found:\n"
        + "\n".join(found)
    )


def _module_level_function(tree: ast.Module, name: str) -> Optional[ast.FunctionDef]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_allowed_sites_still_exist() -> None:
    """A rename must not turn the rule off: each allowed function exists and holds its import."""
    problems: List[str] = []
    for suffix, function, expected in ALLOWED_SITES:
        file = REPO_ROOT / "src" / suffix
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        node = _module_level_function(tree, function)
        if node is None:
            problems.append(f"src/{suffix}: no module-level function {function}()")
            continue
        imported: Set[str] = set()
        for child in ast.walk(node):
            imported.update(_imported_names(child))
        for package in sorted(expected - imported):
            problems.append(f"src/{suffix}: {function}() no longer imports {package}")

    assert problems == []


# ---------------------------------------------------------------------------
# Importing the CLI, in a child interpreter
# ---------------------------------------------------------------------------

_CHILD = """
import json, sys
import plexmix.cli.main
import plexmix.utils.embeddings
import plexmix.ai.local_provider
watched = {names!r}
loaded = sorted(name for name in watched if name in sys.modules)
print("LOADED=" + json.dumps(loaded))
"""

# What the child may inherit: how to find the interpreter's libraries, and nothing else.
# A copy of the whole environment would hand the child whatever keys the shell holds.
_PASS_THROUGH = (
    "PATH",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
)


def _child_environment(home: Path) -> Dict[str, str]:
    data_dir = home / ".plexmix"
    data_dir.mkdir(parents=True, exist_ok=True)
    # A scratch profile holds its own config file, or PlexMix would read the real one.
    (data_dir / "config.yaml").write_text("", encoding="utf-8")
    environment = {name: os.environ[name] for name in _PASS_THROUGH if name in os.environ}
    source = str(REPO_ROOT / "src")
    existing = os.environ.get("PYTHONPATH")
    environment["PYTHONPATH"] = source + os.pathsep + existing if existing else source
    environment["HOME"] = str(home)
    environment["PLEXMIX_DATA_DIR"] = str(data_dir)
    environment["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def test_importing_the_cli_loads_no_faiss_and_no_model_runtime(tmp_path: Path) -> None:
    """A worker that a CLI command spawns re-runs the console script, so the import counts.

    The FAISS part is meaningful in CI. The rest is meaningful where the ``local`` extra is
    installed: nothing then loads torch behind PlexMix's back.
    """
    result = subprocess.run(
        [sys.executable, "-c", _CHILD.format(names=WATCHED_MODULES)],
        cwd=str(tmp_path),
        env=_child_environment(tmp_path / "home"),
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert result.returncode == 0, f"the child failed:\n{result.stderr[-2000:]}"
    lines = [line for line in result.stdout.splitlines() if line.startswith("LOADED=")]
    assert len(lines) == 1, f"no answer from the child:\n{result.stdout[-1000:]}"
    loaded = json.loads(lines[0][len("LOADED=") :])
    assert loaded == [], (
        f"importing the CLI loaded {loaded}. FAISS must load on first use (_faiss) and the "
        "local model runtime only inside a worker."
    )
