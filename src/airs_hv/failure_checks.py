"""
Deterministic failure checks for hallucination-focused artifact evaluation.

This module intentionally uses mock registries and static analysis only.
It never calls package indexes, live APIs, or real system executors.
"""

from __future__ import annotations

import ast
import builtins
import json
import re
import shlex
import symtable
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

MetricResult = Dict[str, Any]
Issue = Dict[str, Any]
FailureReport = Dict[str, Any]

METRIC_DEPENDENCY = "dependency_hallucination_rate"
METRIC_API = "api_symbol_validity_rate"
METRIC_CLI = "cli_command_flag_validity_rate"
METRIC_EXECUTABLE = "executable_integrity_pass_rate"
METRIC_REQUIREMENT = "requirement_artifact_consistency_score"
METRIC_RECURRENT = "recurrent_hallucination_stability_rate"

DEFAULT_STANDARD_LIBRARY_MODULES = {
    "argparse",
    "asyncio",
    "collections",
    "csv",
    "datetime",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "random",
    "re",
    "shlex",
    "sqlite3",
    "statistics",
    "subprocess",
    "sys",
    "tempfile",
    "time",
    "typing",
    "unittest",
    "urllib",
    "uuid",
}
STANDARD_LIBRARY_MODULES = DEFAULT_STANDARD_LIBRARY_MODULES | set(
    getattr(sys, "stdlib_module_names", set())
)

MOCK_PACKAGE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "numpy": {"versions": {"1.26.4", "2.0.2"}},
    "pandas": {"versions": {"2.1.4", "2.2.3"}},
    "requests": {"versions": {"2.31.0", "2.32.3"}},
    "httpx": {"versions": {"0.27.2", "0.28.1"}},
    "typer": {"versions": {"0.12.5", "0.15.1"}},
    "click": {"versions": {"8.1.7", "8.1.8"}},
    "beautifulsoup4": {"versions": {"4.12.3"}},
}

MOCK_API_SCHEMA: Dict[str, Dict[str, Any]] = {
    "requests": {
        "functions": {
            "get": {"min_args": 1, "max_args": 4},
            "post": {"min_args": 1, "max_args": 4},
        },
        "classes": {
            "Session": {
                "methods": {
                    "get": {"min_args": 1, "max_args": 4},
                    "post": {"min_args": 1, "max_args": 4},
                    "close": {"min_args": 0, "max_args": 0},
                }
            }
        },
    },
    "pandas": {
        "functions": {
            "read_csv": {"min_args": 1, "max_args": 6},
            "concat": {"min_args": 1, "max_args": 4},
        },
        "classes": {
            "DataFrame": {
                "methods": {
                    "head": {"min_args": 0, "max_args": 1},
                    "merge": {"min_args": 1, "max_args": 6},
                    "to_csv": {"min_args": 0, "max_args": 5},
                }
            }
        },
    },
    "pathlib": {
        "functions": {},
        "classes": {
            "Path": {
                "methods": {
                    "exists": {"min_args": 0, "max_args": 0},
                    "read_text": {"min_args": 0, "max_args": 2},
                    "write_text": {"min_args": 1, "max_args": 3},
                }
            }
        },
    },
    "json": {
        "functions": {
            "loads": {"min_args": 1, "max_args": 2},
            "dumps": {"min_args": 1, "max_args": 10},
        },
        "classes": {},
    },
}

MOCK_CLI_SCHEMA: Dict[str, Dict[str, Any]] = {
    "git": {
        "flags": {"--help", "--version", "-C"},
        "subcommands": {
            "clone": {"flags": {"--depth", "--branch"}},
            "status": {"flags": {"--short", "--branch"}},
            "commit": {"flags": {"-m", "--amend"}},
        },
    },
    "python": {
        "flags": {"-m", "-c", "--version", "-V"},
        "subcommands": {},
    },
    "pip": {
        "flags": {"--version", "--help"},
        "subcommands": {
            "install": {"flags": {"-r", "--upgrade", "--no-deps"}},
            "show": {"flags": {}},
        },
    },
    "curl": {
        "flags": {"-X", "-H", "-d", "-o", "--fail", "--help"},
        "subcommands": {},
    },
}

RISK_WEIGHTS = {
    METRIC_DEPENDENCY: 0.22,
    METRIC_API: 0.18,
    METRIC_CLI: 0.16,
    METRIC_EXECUTABLE: 0.22,
    METRIC_REQUIREMENT: 0.12,
    METRIC_RECURRENT: 0.10,
}


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, round(value, 4)))


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _normalize_name(value: str) -> str:
    return value.strip().split(".")[0].lower().replace("_", "-")


def _compact_name(value: str) -> str:
    return value.strip().split(".")[0].lower()


def _build_issue(
    issue_type: str,
    artifact: str,
    reason: str,
    *,
    evidence: str | None = None,
    expected: str | None = None,
    metric: str | None = None,
) -> Issue:
    issue: Issue = {
        "type": issue_type,
        "artifact": artifact,
        "reason": reason,
        "fingerprint": f"{issue_type}:{artifact}",
    }
    if evidence:
        issue["evidence"] = evidence
    if expected:
        issue["expected"] = expected
    if metric:
        issue["metric"] = metric
    return issue


def _build_metric_result(
    metric: str,
    score: float,
    issues: Sequence[Issue],
    confidence: float,
) -> MetricResult:
    normalized_score = _clamp(score)
    return {
        "metric": metric,
        "status": "pass" if not issues and normalized_score >= 0.9999 else "fail",
        "score": normalized_score,
        "issues": list(issues),
        "confidence": _clamp(confidence),
    }


def _merge_prompt_spec(prompt_spec: Mapping[str, Any] | None) -> Dict[str, Any]:
    if prompt_spec is None:
        prompt_spec = {}
    elif not isinstance(prompt_spec, Mapping):
        contract = getattr(prompt_spec, "contract", None)
        language = getattr(prompt_spec, "language", None)
        prompt_spec = {
            "contract": contract,
            "artifact_type": "code" if str(language or "").lower() == "python" else None,
        }
    else:
        prompt_spec = dict(prompt_spec)

    merged: Dict[str, Any] = {}
    for nested_key in ("contract", "requirements", "constraints"):
        nested = prompt_spec.get(nested_key)
        if isinstance(nested, Mapping):
            merged.update(nested)
    for key, value in prompt_spec.items():
        if key not in {"contract", "requirements", "constraints"}:
            merged[key] = value
    return merged


def _looks_like_code_artifact(
    artifact: str, metadata: Mapping[str, Any], prompt_spec: Mapping[str, Any]
) -> bool:
    artifact_type = (
        str(metadata.get("artifact_type") or prompt_spec.get("artifact_type") or "")
        .strip()
        .lower()
    )
    if artifact_type in {"code", "python", "script"}:
        return True
    if artifact_type in {"command", "cli", "shell"}:
        return False
    code_markers = ("import ", "from ", "def ", "class ", "\n", "__main__")
    return any(marker in artifact for marker in code_markers)


def _looks_like_cli_artifact_text(artifact: str) -> bool:
    stripped = artifact.strip()
    if not stripped or _looks_like_code_artifact(stripped, {}, {}):
        return False
    if "\n" in stripped:
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if len(lines) != 1:
            return False
        stripped = lines[0]
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False
    if not tokens:
        return False
    head = tokens[0]
    if head.startswith("-"):
        return False
    return len(tokens) > 1 or any(token.startswith("-") for token in tokens[1:])


def _safe_parse_python_artifact(artifact: str) -> tuple[ast.AST | None, SyntaxError | None]:
    try:
        return ast.parse(artifact), None
    except SyntaxError as exc:
        return None, exc


def _parse_import_entry(entry: Any) -> Dict[str, str]:
    if isinstance(entry, Mapping):
        raw_name = str(
            entry.get("name")
            or entry.get("module")
            or entry.get("package")
            or entry.get("import")
            or ""
        )
        version = str(entry.get("version") or "").strip()
        evidence = str(entry.get("source") or entry.get("statement") or raw_name).strip()
    else:
        raw_name = str(entry).strip()
        evidence = raw_name
        version = ""
        if "==" in raw_name:
            raw_name, version = [part.strip() for part in raw_name.split("==", 1)]
    name = raw_name.split()[0].split(".")[0]
    return {
        "name": name,
        "normalized_name": _normalize_name(name),
        "version": version,
        "evidence": evidence or name,
    }


def _parse_api_entry(entry: Any) -> Dict[str, Any]:
    if isinstance(entry, Mapping):
        raw_library = str(entry.get("library") or entry.get("module") or "").strip()
        raw_symbol = str(entry.get("symbol") or entry.get("name") or "").strip()
        if not raw_symbol and raw_library and "." in raw_library:
            raw_library, raw_symbol = raw_library.split(".", 1)
        return {
            "library": raw_library,
            "symbol": raw_symbol,
            "kind": str(entry.get("kind") or "").strip().lower(),
            "args_count": entry.get("args_count"),
            "evidence": str(entry.get("source") or entry.get("call") or raw_symbol or raw_library),
        }

    raw = str(entry).strip()
    if "." in raw:
        library, symbol = raw.split(".", 1)
    else:
        library, symbol = "", raw
    return {
        "library": library,
        "symbol": symbol,
        "kind": "",
        "args_count": None,
        "evidence": raw,
    }


def _parse_cli_entry(entry: Any) -> Dict[str, Any]:
    if isinstance(entry, Mapping):
        command = str(entry.get("command") or "").strip()
        if command:
            tokens = shlex.split(command)
        else:
            tokens = [str(token) for token in _as_list(entry.get("tokens"))]
        tool = str(entry.get("tool") or (tokens[0] if tokens else "")).strip()
    else:
        command = str(entry).strip()
        tokens = shlex.split(command)
        tool = tokens[0] if tokens else ""

    subcommand = ""
    flags: List[str] = []
    if tokens:
        tail = tokens[1:] if tool else tokens
        for token in tail:
            if token.startswith("-"):
                flags.append(token)
            elif not subcommand:
                subcommand = token

    return {
        "tool": tool,
        "subcommand": subcommand,
        "flags": flags,
        "tokens": tokens,
        "evidence": command or " ".join(tokens),
    }


def _extract_imports_from_tree(tree: ast.AST) -> List[Dict[str, str]]:
    imports: List[Dict[str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    {
                        "name": alias.name.split(".")[0],
                        "normalized_name": _normalize_name(alias.name),
                        "version": "",
                        "evidence": f"import {alias.name}",
                    }
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(
                {
                    "name": node.module.split(".")[0],
                    "normalized_name": _normalize_name(node.module),
                    "version": "",
                    "evidence": f"from {node.module} import ...",
                }
            )

    return imports


def _extract_imports_from_code(artifact: str) -> List[Dict[str, str]]:
    tree, _ = _safe_parse_python_artifact(artifact)
    if tree is None:
        return []
    return _extract_imports_from_tree(tree)


def _normalize_imports(artifact: str, metadata: Mapping[str, Any]) -> List[Dict[str, str]]:
    imports = [_parse_import_entry(entry) for entry in _as_list(metadata.get("imports"))]
    pip_installs = [_parse_import_entry(entry) for entry in _as_list(metadata.get("pip_installs"))]
    if not imports and _looks_like_code_artifact(artifact, metadata, {}):
        imports = _extract_imports_from_code(artifact)
    return imports + pip_installs


def _normalize_api_calls(metadata: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [_parse_api_entry(entry) for entry in _as_list(metadata.get("api_calls"))]


def _normalize_cli_calls(artifact: str, metadata: Mapping[str, Any]) -> List[Dict[str, Any]]:
    calls = [_parse_cli_entry(entry) for entry in _as_list(metadata.get("cli_calls"))]
    artifact_type = str(metadata.get("artifact_type") or "").lower()
    if not calls and (
        artifact_type in {"command", "cli", "shell"}
        or _looks_like_cli_artifact_text(artifact)
    ):
        calls.append(_parse_cli_entry(artifact))
    return calls


def _flatten_callable_reference(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _flatten_callable_reference(node.value)
        if parent:
            return f"{parent}.{node.attr}"
        return None
    if isinstance(node, ast.Call):
        called = _flatten_callable_reference(node.func)
        if called:
            return f"{called}()"
    return None


def _collect_import_alias_targets(tree: ast.AST) -> Dict[str, str]:
    alias_targets: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.name.split(".")[0]
                alias_targets[alias.asname or root_name] = root_name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                alias_targets[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return alias_targets


def _canonicalize_reference(
    reference: str | None,
    alias_targets: Mapping[str, str],
    instance_targets: Mapping[str, str],
) -> str | None:
    if not reference:
        return None

    normalized = reference.replace("().", ".").replace("()", "")
    parts = normalized.split(".")
    head = parts[0]
    replacement = instance_targets.get(head) or alias_targets.get(head)
    if replacement:
        parts = replacement.split(".") + parts[1:]
    return ".".join(part for part in parts if part)


def _extract_api_calls_from_tree(
    tree: ast.AST,
    api_schema: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    schema = api_schema or MOCK_API_SCHEMA
    alias_targets = _collect_import_alias_targets(tree)
    instance_targets: Dict[str, str] = {}
    imported_roots = {
        value.split(".", 1)[0]
        for value in alias_targets.values()
        if value
    }
    known_classes = {
        f"{library}.{class_name}"
        for library, spec in schema.items()
        for class_name in spec.get("classes", {})
    }

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets: List[ast.expr] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value

        if not isinstance(value, ast.Call):
            continue

        instance_ref = _canonicalize_reference(
            _flatten_callable_reference(value.func),
            alias_targets,
            instance_targets,
        )
        if instance_ref not in known_classes:
            continue

        for target in targets:
            if isinstance(target, ast.Name):
                instance_targets[target.id] = instance_ref

    api_calls: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        canonical = _canonicalize_reference(
            _flatten_callable_reference(node.func),
            alias_targets,
            instance_targets,
        )
        if not canonical or "." not in canonical:
            continue

        library, symbol = canonical.split(".", 1)
        if library not in imported_roots and library not in schema:
            continue

        api_calls.append(
            {
                "library": library,
                "symbol": symbol,
                "kind": "",
                "args_count": len(node.args),
                "evidence": canonical,
            }
        )

    return api_calls


def _extract_constant_cli_entry(argument: ast.AST) -> Dict[str, Any] | None:
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return _parse_cli_entry(argument.value)

    if isinstance(argument, (ast.List, ast.Tuple)):
        tokens: List[str] = []
        for element in argument.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                return None
            tokens.append(element.value)
        if tokens:
            return _parse_cli_entry({"tokens": tokens})

    return None


def _extract_cli_calls_from_tree(tree: ast.AST) -> List[Dict[str, Any]]:
    alias_targets = _collect_import_alias_targets(tree)
    shell_call_targets = {
        "os.system",
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
    }

    calls: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        canonical = _canonicalize_reference(
            _flatten_callable_reference(node.func),
            alias_targets,
            {},
        )
        if canonical not in shell_call_targets or not node.args:
            continue
        cli_entry = _extract_constant_cli_entry(node.args[0])
        if cli_entry:
            calls.append(cli_entry)

    return calls


def _prepare_artifact_metadata(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    prepared = dict(metadata or {})
    merged_prompt_spec = _merge_prompt_spec(prompt_spec)
    artifact_type = str(prepared.get("artifact_type") or "").strip().lower()

    if not artifact_type:
        if _looks_like_code_artifact(artifact, prepared, merged_prompt_spec):
            artifact_type = "code"
        elif _looks_like_cli_artifact_text(artifact):
            artifact_type = "command"
        else:
            artifact_type = "text"
        prepared["artifact_type"] = artifact_type

    if artifact_type in {"code", "python", "script"}:
        tree, syntax_error = _safe_parse_python_artifact(artifact)
        if syntax_error is not None:
            prepared["python_syntax_error"] = {
                "lineno": syntax_error.lineno,
                "msg": syntax_error.msg,
            }
        if not prepared.get("imports") and tree is not None:
            prepared["imports"] = _extract_imports_from_tree(tree)
        if not prepared.get("api_calls") and tree is not None:
            prepared["api_calls"] = _extract_api_calls_from_tree(tree)
        if not prepared.get("cli_calls") and tree is not None:
            prepared["cli_calls"] = _extract_cli_calls_from_tree(tree)

    if not prepared.get("cli_calls") and (
        artifact_type in {"command", "cli", "shell"}
        or _looks_like_cli_artifact_text(artifact)
    ):
        prepared["artifact_type"] = "command"
        prepared["cli_calls"] = [_parse_cli_entry(artifact)]

    prepared.setdefault("pip_installs", [])
    return prepared


def prepare_artifact_metadata(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Normalize and enrich artifact metadata for downstream metric checks."""
    return _prepare_artifact_metadata(artifact, metadata, prompt_spec)


def _package_exists(package_name: str, package_registry: Mapping[str, Any]) -> bool:
    normalized_name = _normalize_name(package_name)
    compact_name = _compact_name(package_name)
    return (
        normalized_name in package_registry
        or compact_name in STANDARD_LIBRARY_MODULES
        or normalized_name.replace("-", "_") in STANDARD_LIBRARY_MODULES
    )


def _version_exists(
    package_name: str,
    version: str,
    package_registry: Mapping[str, Any],
) -> bool:
    if not version:
        return True
    normalized_name = _normalize_name(package_name)
    package_spec = package_registry.get(normalized_name, {})
    versions = set(package_spec.get("versions", set()))
    return version in versions


def validate_package_dependency(
    package_name: str,
    version: str = "",
    *,
    package_registry: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    registry = package_registry or MOCK_PACKAGE_REGISTRY
    exists = _package_exists(package_name, registry)
    version_valid = exists and _version_exists(package_name, version, registry)
    return {
        "package": package_name,
        "exists": exists,
        "version": version,
        "version_valid": version_valid,
    }


def validate_api_symbol(
    library: str,
    symbol: str,
    *,
    kind: str = "",
    args_count: int | None = None,
    api_schema: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    schema = api_schema or MOCK_API_SCHEMA
    library_key = library.split(".")[0]
    library_schema = schema.get(library_key)
    if not library_schema:
        return {
            "library": library,
            "symbol": symbol,
            "exists": False,
            "validated": True,
            "reason": f"Library '{library_key}' is not present in the mock API schema.",
        }

    functions = library_schema.get("functions", {})
    classes = library_schema.get("classes", {})

    if "." in symbol:
        class_name, method_name = symbol.split(".", 1)
        class_spec = classes.get(class_name)
        if not class_spec:
            return {
                "library": library,
                "symbol": symbol,
                "exists": False,
                "validated": True,
                "reason": f"Class '{class_name}' is not present in the mock schema for '{library_key}'.",
            }
        method_spec = class_spec.get("methods", {}).get(method_name)
        if not method_spec:
            return {
                "library": library,
                "symbol": symbol,
                "exists": False,
                "validated": True,
                "reason": f"Method '{method_name}' is not defined on '{class_name}' in the mock schema.",
            }
        if kind and kind not in {"method", ""}:
            return {
                "library": library,
                "symbol": symbol,
                "exists": False,
                "validated": True,
                "reason": f"Symbol '{symbol}' is a method, not a '{kind}'.",
            }
        if args_count is not None and not (
            method_spec["min_args"] <= args_count <= method_spec["max_args"]
        ):
            return {
                "library": library,
                "symbol": symbol,
                "exists": False,
                "validated": True,
                "reason": (
                    f"Method '{symbol}' expects between {method_spec['min_args']} and "
                    f"{method_spec['max_args']} positional arguments in the mock schema."
                ),
            }
        return {
            "library": library,
            "symbol": symbol,
            "exists": True,
            "validated": True,
            "reason": "Symbol validated against the mock class-method schema.",
        }

    function_spec = functions.get(symbol)
    if function_spec:
        if kind and kind not in {"function", ""}:
            return {
                "library": library,
                "symbol": symbol,
                "exists": False,
                "validated": True,
                "reason": f"Symbol '{symbol}' is a function, not a '{kind}'.",
            }
        if args_count is not None and not (
            function_spec["min_args"] <= args_count <= function_spec["max_args"]
        ):
            return {
                "library": library,
                "symbol": symbol,
                "exists": False,
                "validated": True,
                "reason": (
                    f"Function '{symbol}' expects between {function_spec['min_args']} and "
                    f"{function_spec['max_args']} positional arguments in the mock schema."
                ),
            }
        return {
            "library": library,
            "symbol": symbol,
            "exists": True,
            "validated": True,
            "reason": "Symbol validated against the mock function schema.",
        }

    class_spec = classes.get(symbol)
    if class_spec:
        if kind and kind not in {"class", ""}:
            return {
                "library": library,
                "symbol": symbol,
                "exists": False,
                "validated": True,
                "reason": f"Symbol '{symbol}' is a class, not a '{kind}'.",
            }
        return {
            "library": library,
            "symbol": symbol,
            "exists": True,
            "validated": True,
            "reason": "Symbol validated against the mock class schema.",
        }

    return {
        "library": library,
        "symbol": symbol,
        "exists": False,
        "validated": True,
        "reason": f"Symbol '{symbol}' is not present in the mock schema for '{library_key}'.",
    }


def validate_cli_command(
    cli_call: Mapping[str, Any],
    *,
    cli_schema: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    schema = cli_schema or MOCK_CLI_SCHEMA
    tool = str(cli_call.get("tool") or "").strip()
    subcommand = str(cli_call.get("subcommand") or "").strip()
    flags = [str(flag) for flag in _as_list(cli_call.get("flags"))]

    if tool not in schema:
        return {
            "tool": tool,
            "subcommand": subcommand,
            "unknown_tool": True,
            "invalid_subcommand": False,
            "invalid_flags": flags,
        }

    tool_spec = schema[tool]
    invalid_subcommand = False
    allowed_flags = set(tool_spec.get("flags", set()))

    if subcommand:
        subcommands = tool_spec.get("subcommands", {})
        if subcommand not in subcommands:
            invalid_subcommand = True
        else:
            allowed_flags |= set(subcommands[subcommand].get("flags", set()))

    invalid_flags = [flag for flag in flags if flag not in allowed_flags]
    return {
        "tool": tool,
        "subcommand": subcommand,
        "unknown_tool": False,
        "invalid_subcommand": invalid_subcommand,
        "invalid_flags": invalid_flags,
    }


def _defined_symbols(table: symtable.SymbolTable) -> set[str]:
    defined: set[str] = set()
    for symbol in table.get_symbols():
        if (
            symbol.is_parameter()
            or symbol.is_imported()
            or symbol.is_assigned()
            or symbol.is_namespace()
        ):
            defined.add(symbol.get_name())
    return defined


def _find_unresolved_names(artifact: str) -> List[str]:
    try:
        root = symtable.symtable(artifact, "<artifact>", "exec")
    except SyntaxError:
        return []

    builtin_names = set(dir(builtins))
    unresolved: set[str] = set()

    def walk(table: symtable.SymbolTable, visible_names: set[str]) -> None:
        local_defs = visible_names | _defined_symbols(table)

        if table.get_type() == "module":
            for symbol in table.get_symbols():
                name = symbol.get_name()
                if symbol.is_referenced() and name not in local_defs and name not in builtin_names:
                    unresolved.add(name)
        else:
            for symbol in table.get_symbols():
                name = symbol.get_name()
                if (
                    symbol.is_referenced()
                    and (symbol.is_global() or symbol.is_free())
                    and name not in visible_names
                    and name not in builtin_names
                ):
                    unresolved.add(name)

        for child in table.get_children():
            walk(child, local_defs)

    walk(root, set())
    return sorted(unresolved)


def _normalize_pip_requirement(entry: Any) -> str | None:
    if isinstance(entry, Mapping):
        name = str(
            entry.get("name")
            or entry.get("package")
            or entry.get("module")
            or ""
        ).strip()
        version = str(entry.get("version") or "").strip()
        if not name:
            return None
        return f"{name}=={version}" if version else name

    raw = str(entry).strip()
    if not raw:
        return None
    if raw.startswith("pip install "):
        raw = raw[len("pip install ") :].strip()
    if raw.startswith("-"):
        return None
    return raw


def _build_sandbox_requirements(metadata: Mapping[str, Any]) -> List[str]:
    requirements: List[str] = []
    for entry in _as_list(metadata.get("pip_installs")):
        normalized = _normalize_pip_requirement(entry)
        if normalized and normalized not in requirements:
            requirements.append(normalized)
    return requirements


def run_python_artifact_in_sandbox(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Execute a Python artifact in the Docker sandbox and return normalized output.

    The sandbox is treated as authoritative for EIPR: if execution cannot be
    performed or exits non-zero, the integrity check must fail.
    """

    normalized_metadata = dict(metadata or {})
    try:
        from .sandbox.run_in_sandbox import run_in_sandbox

        with tempfile.TemporaryDirectory(prefix="airs_hv_exec_") as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "main.py").write_text(artifact, encoding="utf-8")

            requirements = _build_sandbox_requirements(normalized_metadata)
            if requirements:
                (workspace / "requirements.txt").write_text(
                    "\n".join(requirements) + "\n",
                    encoding="utf-8",
                )

            result = run_in_sandbox(workspace)
            return {
                "executed": True,
                "sandboxed": True,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration": result.duration,
            }
    except Exception as exc:
        return {
            "executed": False,
            "sandboxed": True,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Sandbox unavailable: {type(exc).__name__}: {exc}",
            "duration": 0.0,
        }


def simulate_sandbox_execution(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    *,
    package_registry: Mapping[str, Any] | None = None,
    api_schema: Mapping[str, Any] | None = None,
    cli_schema: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Combined static and sandbox-backed execution check.

    For Python artifacts this performs real Docker sandbox execution in
    addition to static validation. For CLI artifacts it performs deterministic
    command validation against the mock CLI schema.
    """

    normalized_metadata = _prepare_artifact_metadata(artifact, metadata, {})
    issues: List[Issue] = []
    observations: List[str] = []
    checks_run = 0
    checks_passed = 0

    if _looks_like_code_artifact(artifact, normalized_metadata, {}):
        checks_run += 1
        try:
            ast.parse(artifact)
            checks_passed += 1
            observations.append("Python syntax parsed successfully.")
        except SyntaxError as exc:
            issues.append(
                _build_issue(
                    "syntax_error",
                    f"line {exc.lineno}",
                    "Artifact could not be parsed as Python, so execution would fail.",
                    evidence=exc.msg,
                    metric=METRIC_EXECUTABLE,
                )
            )

        checks_run += 1
        unresolved_names = _find_unresolved_names(artifact)
        if unresolved_names:
            for name in unresolved_names:
                issues.append(
                    _build_issue(
                        "runtime_reference_error",
                        name,
                        "Static execution simulation found a referenced name with no visible definition.",
                        metric=METRIC_EXECUTABLE,
                    )
                )
        else:
            checks_passed += 1
            observations.append("No unresolved variable references were detected.")

        imports = _normalize_imports(artifact, normalized_metadata)
        checks_run += 1
        import_failures = []
        for item in imports:
            result = validate_package_dependency(
                item["name"],
                item["version"],
                package_registry=package_registry,
            )
            if not result["exists"] or not result["version_valid"]:
                import_failures.append(item)
        if import_failures:
            for item in import_failures:
                issues.append(
                    _build_issue(
                        "unresolvable_import",
                        item["name"],
                        "Sandbox import simulation could not resolve this dependency.",
                        evidence=item["evidence"],
                        metric=METRIC_EXECUTABLE,
                    )
                )
        else:
            checks_passed += 1
            observations.append("All referenced imports resolved in the mock registry.")

        api_calls = _normalize_api_calls(normalized_metadata)
        if api_calls:
            checks_run += 1
            api_failures = []
            for api_call in api_calls:
                library = api_call["library"]
                symbol = api_call["symbol"]
                if not library or not symbol:
                    continue
                result = validate_api_symbol(
                    library,
                    symbol,
                    kind=str(api_call.get("kind") or ""),
                    args_count=api_call.get("args_count"),
                    api_schema=api_schema,
                )
                if result["validated"] and not result["exists"]:
                    api_failures.append((api_call, result))
            if api_failures:
                for api_call, result in api_failures:
                    issues.append(
                        _build_issue(
                            "runtime_symbol_failure",
                            f"{api_call['library']}.{api_call['symbol']}",
                            "Static execution simulation predicts a runtime failure for this API symbol.",
                            evidence=result["reason"],
                            metric=METRIC_EXECUTABLE,
                        )
                    )
            else:
                checks_passed += 1
                observations.append("Referenced API symbols are executable within the mock schema.")

        if re.search(r"\braise\s+(Exception|RuntimeError|ValueError)\b", artifact):
            issues.append(
                _build_issue(
                    "explicit_runtime_failure",
                    "raise",
                    "Artifact contains an explicit exception path that would fail execution.",
                    evidence="raise Exception/RuntimeError/ValueError detected",
                    metric=METRIC_EXECUTABLE,
                )
            )
        elif "sys.exit(1)" in artifact or "SystemExit(1)" in artifact:
            issues.append(
                _build_issue(
                    "explicit_runtime_failure",
                    "sys.exit(1)",
                    "Artifact contains an explicit non-zero exit path.",
                    metric=METRIC_EXECUTABLE,
                )
            )

        checks_run += 1
        sandbox_execution = run_python_artifact_in_sandbox(
            artifact,
            normalized_metadata,
        )
        if sandbox_execution["exit_code"] != 0:
            issues.append(
                _build_issue(
                    "sandbox_execution_failed",
                    f"exit_code={sandbox_execution['exit_code']}",
                    "Artifact failed during Docker sandbox execution.",
                    evidence=sandbox_execution.get("stderr") or sandbox_execution.get("stdout") or "",
                    metric=METRIC_EXECUTABLE,
                )
            )
        else:
            checks_passed += 1
            observations.append("Artifact executed successfully in the Docker sandbox.")

    else:
        cli_calls = _normalize_cli_calls(artifact, normalized_metadata)
        if cli_calls:
            checks_run += 1
            cli_failures = []
            for cli_call in cli_calls:
                result = validate_cli_command(cli_call, cli_schema=cli_schema)
                if (
                    result["unknown_tool"]
                    or result["invalid_subcommand"]
                    or result["invalid_flags"]
                ):
                    cli_failures.append((cli_call, result))
            if cli_failures:
                for cli_call, result in cli_failures:
                    issues.append(
                        _build_issue(
                            "command_execution_failure",
                            cli_call["evidence"],
                            "Sandbox command simulation predicts this command would fail validation.",
                            evidence=json.dumps(result, sort_keys=True),
                            metric=METRIC_EXECUTABLE,
                        )
                    )
            else:
                checks_passed += 1
                observations.append("CLI artifact passes mock command validation.")
        else:
            observations.append("No runnable code or CLI command was detected.")

    total_checks = max(checks_run, 1)
    score = checks_passed / total_checks
    return {
        "status": "pass" if not issues else "fail",
        "score": _clamp(score),
        "issues": issues,
        "confidence": 0.9 if checks_run else 0.6,
        "observations": observations,
        "sandboxed": True,
        "executed": _looks_like_code_artifact(artifact, normalized_metadata, {}),
    }


def check_dependency_hallucination(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
    historical_outputs: Sequence[Mapping[str, Any]] | None = None,
    *,
    package_registry: Mapping[str, Any] | None = None,
) -> MetricResult:
    del historical_outputs

    normalized_metadata = _prepare_artifact_metadata(artifact, metadata, prompt_spec)
    dependencies = _normalize_imports(artifact, normalized_metadata)
    if not dependencies:
        syntax_error = normalized_metadata.get("python_syntax_error")
        if syntax_error:
            issues = [
                _build_issue(
                    "dependency_scan_error",
                    f"line {syntax_error.get('lineno')}",
                    "Dependency scan could not parse the artifact, so import extraction is incomplete.",
                    evidence=str(syntax_error.get("msg") or ""),
                    metric=METRIC_DEPENDENCY,
                )
            ]
            return _build_metric_result(METRIC_DEPENDENCY, 0.0, issues, 0.9)
        return _build_metric_result(METRIC_DEPENDENCY, 1.0, [], 0.75)

    registry = package_registry or MOCK_PACKAGE_REGISTRY
    issues: List[Issue] = []
    valid_count = 0

    for dependency in dependencies:
        result = validate_package_dependency(
            dependency["name"],
            dependency["version"],
            package_registry=registry,
        )
        if not result["exists"]:
            issues.append(
                _build_issue(
                    "nonexistent_package",
                    dependency["name"],
                    "Dependency is not present in the mock package registry or standard library allowlist.",
                    evidence=dependency["evidence"],
                    metric=METRIC_DEPENDENCY,
                )
            )
            continue
        if dependency["version"] and not result["version_valid"]:
            issues.append(
                _build_issue(
                    "version_not_found",
                    f"{dependency['name']}=={dependency['version']}",
                    "Requested dependency version is not available in the mock package registry.",
                    evidence=dependency["evidence"],
                    metric=METRIC_DEPENDENCY,
                )
            )
            continue
        valid_count += 1

    score = valid_count / max(len(dependencies), 1)
    return _build_metric_result(METRIC_DEPENDENCY, score, issues, 0.96)


def check_api_validity(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
    historical_outputs: Sequence[Mapping[str, Any]] | None = None,
    *,
    api_schema: Mapping[str, Any] | None = None,
) -> MetricResult:
    del historical_outputs

    normalized_metadata = _prepare_artifact_metadata(artifact, metadata, prompt_spec)
    api_calls = _normalize_api_calls(normalized_metadata)
    if not api_calls:
        return _build_metric_result(METRIC_API, 1.0, [], 0.7)

    schema = api_schema or MOCK_API_SCHEMA
    issues: List[Issue] = []
    validated = 0
    valid_count = 0

    for api_call in api_calls:
        library = str(api_call.get("library") or "").strip()
        symbol = str(api_call.get("symbol") or "").strip()
        if not library or not symbol:
            validated += 1
            issues.append(
                _build_issue(
                    "malformed_api_call",
                    str(api_call.get("evidence") or api_call),
                    "API metadata is missing a library or symbol name, so the reference cannot be validated.",
                    metric=METRIC_API,
                )
            )
            continue

        result = validate_api_symbol(
            library,
            symbol,
            kind=str(api_call.get("kind") or ""),
            args_count=api_call.get("args_count"),
            api_schema=schema,
        )

        validated += 1
        if result["exists"]:
            valid_count += 1
            continue

        issues.append(
            _build_issue(
                "invalid_api_symbol",
                f"{library}.{symbol}",
                "Referenced API symbol could not be verified in the mock library schema.",
                evidence=result["reason"],
                metric=METRIC_API,
            )
        )

    score = valid_count / validated
    confidence = 0.93 if validated == len(api_calls) else 0.7
    return _build_metric_result(METRIC_API, score, issues, confidence)


def check_cli_validity(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
    historical_outputs: Sequence[Mapping[str, Any]] | None = None,
    *,
    cli_schema: Mapping[str, Any] | None = None,
) -> MetricResult:
    del historical_outputs

    normalized_metadata = _prepare_artifact_metadata(artifact, metadata, prompt_spec)
    cli_calls = _normalize_cli_calls(artifact, normalized_metadata)
    if not cli_calls:
        return _build_metric_result(METRIC_CLI, 1.0, [], 0.75)

    schema = cli_schema or MOCK_CLI_SCHEMA
    issues: List[Issue] = []
    total_units = 0
    valid_units = 0

    for cli_call in cli_calls:
        result = validate_cli_command(cli_call, cli_schema=schema)
        total_units += 1
        if not result["unknown_tool"]:
            valid_units += 1
        else:
            issues.append(
                _build_issue(
                    "cli_not_found",
                    cli_call["tool"] or cli_call["evidence"],
                    "CLI tool does not exist in the mock command registry.",
                    evidence=cli_call["evidence"],
                    metric=METRIC_CLI,
                )
            )
            continue

        if cli_call["subcommand"]:
            total_units += 1
            if result["invalid_subcommand"]:
                issues.append(
                    _build_issue(
                        "invalid_subcommand",
                        f"{cli_call['tool']} {cli_call['subcommand']}",
                        "CLI subcommand is not defined in the mock command schema.",
                        evidence=cli_call["evidence"],
                        metric=METRIC_CLI,
                    )
                )
            else:
                valid_units += 1

        for flag in cli_call["flags"]:
            total_units += 1
            if flag in result["invalid_flags"]:
                issues.append(
                    _build_issue(
                        "invalid_flag",
                        f"{cli_call['tool']} {flag}",
                        "CLI flag is not supported by the mock command schema.",
                        evidence=cli_call["evidence"],
                        metric=METRIC_CLI,
                    )
                )
            else:
                valid_units += 1

    score = valid_units / max(total_units, 1)
    return _build_metric_result(METRIC_CLI, score, issues, 0.95)


def check_executable_integrity(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
    historical_outputs: Sequence[Mapping[str, Any]] | None = None,
    *,
    package_registry: Mapping[str, Any] | None = None,
    api_schema: Mapping[str, Any] | None = None,
    cli_schema: Mapping[str, Any] | None = None,
) -> MetricResult:
    del historical_outputs

    normalized_metadata = _prepare_artifact_metadata(artifact, metadata, prompt_spec)
    sandbox_result = simulate_sandbox_execution(
        artifact,
        normalized_metadata,
        package_registry=package_registry,
        api_schema=api_schema,
        cli_schema=cli_schema,
    )
    issues = list(sandbox_result["issues"])
    confidence = sandbox_result["confidence"]
    if sandbox_result["observations"]:
        confidence = min(0.98, confidence + 0.02 * len(sandbox_result["observations"]))
    return _build_metric_result(
        METRIC_EXECUTABLE,
        float(sandbox_result["score"]),
        issues,
        confidence,
    )


def check_requirement_consistency(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
    historical_outputs: Sequence[Mapping[str, Any]] | None = None,
) -> MetricResult:
    del historical_outputs

    normalized_metadata = _prepare_artifact_metadata(artifact, metadata, prompt_spec)
    constraints = _merge_prompt_spec(prompt_spec)
    if not constraints:
        return _build_metric_result(METRIC_REQUIREMENT, 1.0, [], 0.65)

    imports = {entry["name"] for entry in _normalize_imports(artifact, normalized_metadata)}
    api_calls = {
        f"{entry['library']}.{entry['symbol']}"
        for entry in _normalize_api_calls(normalized_metadata)
        if entry.get("library") and entry.get("symbol")
    }
    cli_calls = _normalize_cli_calls(artifact, normalized_metadata)
    cli_tools = {entry["tool"] for entry in cli_calls if entry.get("tool")}
    artifact_lower = artifact.lower()
    issue_list: List[Issue] = []
    total_checks = 0
    passed_checks = 0

    def require(condition: bool, issue: Issue) -> None:
        nonlocal total_checks, passed_checks
        total_checks += 1
        if condition:
            passed_checks += 1
        else:
            issue_list.append(issue)

    for package_name in _as_list(constraints.get("required_imports")):
        require(
            str(package_name) in imports,
            _build_issue(
                "missing_required_import",
                str(package_name),
                "Prompt required this import, but it is missing from parsed metadata.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    for package_name in _as_list(constraints.get("forbidden_imports")):
        require(
            str(package_name) not in imports,
            _build_issue(
                "forbidden_import_used",
                str(package_name),
                "Prompt forbids this import, but it appears in the generated artifact.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    for api_name in _as_list(constraints.get("required_apis")):
        require(
            str(api_name) in api_calls,
            _build_issue(
                "missing_required_api",
                str(api_name),
                "Prompt required this API symbol, but it is missing from parsed metadata.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    for api_name in _as_list(constraints.get("forbidden_apis")):
        require(
            str(api_name) not in api_calls,
            _build_issue(
                "forbidden_api_used",
                str(api_name),
                "Prompt forbids this API symbol, but it appears in parsed metadata.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    for tool_name in _as_list(constraints.get("required_commands")):
        require(
            str(tool_name) in cli_tools,
            _build_issue(
                "missing_required_command",
                str(tool_name),
                "Prompt required this CLI tool, but it is missing from parsed metadata.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    for tool_name in _as_list(constraints.get("forbidden_commands")):
        require(
            str(tool_name) not in cli_tools,
            _build_issue(
                "forbidden_command_used",
                str(tool_name),
                "Prompt forbids this CLI tool, but it appears in parsed metadata.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    for term in _as_list(
        constraints.get("required_terms") or constraints.get("must_contain")
    ):
        require(
            str(term).lower() in artifact_lower,
            _build_issue(
                "missing_required_term",
                str(term),
                "Prompt required this term or phrase, but it does not appear in the artifact text.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    for term in _as_list(
        constraints.get("forbidden_terms") or constraints.get("must_not_contain")
    ):
        require(
            str(term).lower() not in artifact_lower,
            _build_issue(
                "forbidden_term_used",
                str(term),
                "Prompt forbids this term or phrase, but it appears in the artifact text.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    for symbol_name in _as_list(constraints.get("must_define")):
        pattern = rf"\b(def|class)\s+{re.escape(str(symbol_name))}\b"
        require(
            re.search(pattern, artifact) is not None,
            _build_issue(
                "missing_required_definition",
                str(symbol_name),
                "Prompt required this definition, but it is missing from the artifact.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    if "artifact_type" in constraints:
        expected_type = str(constraints["artifact_type"]).strip().lower()
        actual_type = "code" if _looks_like_code_artifact(artifact, normalized_metadata, constraints) else "command"
        require(
            expected_type == actual_type,
            _build_issue(
                "artifact_type_mismatch",
                actual_type,
                "Generated artifact type does not match the prompt constraint.",
                expected=expected_type,
                metric=METRIC_REQUIREMENT,
            ),
        )

    if "max_lines" in constraints:
        max_lines = int(constraints["max_lines"])
        actual_lines = len([line for line in artifact.splitlines() if line.strip()])
        require(
            actual_lines <= max_lines,
            _build_issue(
                "line_budget_exceeded",
                str(actual_lines),
                "Generated artifact exceeds the maximum line budget from the prompt specification.",
                expected=str(max_lines),
                metric=METRIC_REQUIREMENT,
            ),
        )

    if constraints.get("disallow_shell"):
        require(
            not cli_calls,
            _build_issue(
                "shell_usage_forbidden",
                "shell",
                "Prompt disallows shell usage, but CLI metadata was detected.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    if constraints.get("disallow_network"):
        network_markers = ("requests.", "httpx.", "urllib.", "curl ", "wget ")
        require(
            not any(marker in artifact for marker in network_markers),
            _build_issue(
                "network_usage_forbidden",
                "network",
                "Prompt disallows network access, but network-related code markers were detected.",
                metric=METRIC_REQUIREMENT,
            ),
        )

    score = 1.0 if total_checks == 0 else passed_checks / total_checks
    return _build_metric_result(METRIC_REQUIREMENT, score, issue_list, 0.88)


def _detect_hallucination_candidates(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
) -> List[Issue]:
    normalized_metadata = _prepare_artifact_metadata(artifact, metadata, prompt_spec)
    metric_results = (
        check_dependency_hallucination(artifact, normalized_metadata, prompt_spec, []),
        check_api_validity(artifact, normalized_metadata, prompt_spec, []),
        check_cli_validity(artifact, normalized_metadata, prompt_spec, []),
        check_executable_integrity(artifact, normalized_metadata, prompt_spec, []),
        check_requirement_consistency(artifact, normalized_metadata, prompt_spec, []),
    )
    return _flatten_hallucination_flags(metric_results)


def _collect_historical_fingerprints(
    historical_outputs: Sequence[Mapping[str, Any]] | None,
) -> Counter[str]:
    fingerprints: Counter[str] = Counter()
    for record in historical_outputs or []:
        if not isinstance(record, Mapping):
            continue

        for flag in _as_list(record.get("hallucination_flags")):
            if isinstance(flag, Mapping):
                fingerprint = str(
                    flag.get("fingerprint")
                    or f"{flag.get('type', 'unknown')}:{flag.get('artifact', '')}"
                )
                if fingerprint:
                    fingerprints[fingerprint] += 1

        for metric_result in _as_list(record.get("metric_results")):
            if not isinstance(metric_result, Mapping):
                continue
            for issue in _as_list(metric_result.get("issues")):
                if isinstance(issue, Mapping):
                    fingerprint = str(
                        issue.get("fingerprint")
                        or f"{issue.get('type', 'unknown')}:{issue.get('artifact', '')}"
                    )
                    if fingerprint:
                        fingerprints[fingerprint] += 1

        if "artifact" in record:
            artifact = str(record.get("artifact") or "")
            metadata = record.get("metadata")
            prompt_spec = record.get("prompt_spec") or record.get("contract")
            for issue in _detect_hallucination_candidates(artifact, metadata, prompt_spec):
                fingerprints[issue["fingerprint"]] += 1

    return fingerprints


def check_recurrent_hallucination(
    artifact: str,
    metadata: Mapping[str, Any] | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
    historical_outputs: Sequence[Mapping[str, Any]] | None = None,
) -> MetricResult:
    normalized_metadata = _prepare_artifact_metadata(artifact, metadata, prompt_spec)
    current_candidates = _detect_hallucination_candidates(
        artifact,
        normalized_metadata,
        prompt_spec,
    )
    if not current_candidates:
        return _build_metric_result(METRIC_RECURRENT, 1.0, [], 0.9)

    historical_fingerprints = _collect_historical_fingerprints(historical_outputs)
    issues: List[Issue] = []
    repeated = 0

    for candidate in current_candidates:
        seen_count = historical_fingerprints.get(candidate["fingerprint"], 0)
        if seen_count > 0:
            repeated += 1
            issues.append(
                _build_issue(
                    "recurrent_hallucination",
                    candidate["artifact"],
                    "The same hallucinated artifact appeared in historical outputs.",
                    evidence=f"Seen {seen_count} previous time(s).",
                    metric=METRIC_RECURRENT,
                )
            )

    score = 1.0 - (repeated / max(len(current_candidates), 1))
    confidence = 0.92 if historical_outputs else 0.65
    return _build_metric_result(METRIC_RECURRENT, score, issues, confidence)


def _flatten_hallucination_flags(metric_results: Sequence[MetricResult]) -> List[Issue]:
    flags: List[Issue] = []
    for result in metric_results:
        for issue in result["issues"]:
            flag = dict(issue)
            flag["metric"] = result["metric"]
            flags.append(flag)
    return flags


def _compute_risk_score(metric_results: Sequence[MetricResult]) -> float:
    weighted_risk = 0.0
    total_weight = 0.0
    failure_count = 0

    for result in metric_results:
        metric = str(result["metric"])
        weight = RISK_WEIGHTS.get(metric, 0.1)
        weighted_risk += weight * (1.0 - float(result["score"]))
        total_weight += weight
        if result["status"] == "fail":
            failure_count += 1

    if total_weight == 0:
        return 0.0

    base_risk = weighted_risk / total_weight
    failure_penalty = min(0.15, failure_count * 0.025)
    return _clamp(base_risk + failure_penalty)


def run_failure_checks(
    artifact_id: str,
    artifact: str,
    metadata: Mapping[str, Any] | None,
    prompt_spec: Mapping[str, Any] | None,
    historical_outputs: Sequence[Mapping[str, Any]] | None = None,
) -> FailureReport:
    if prompt_spec is None:
        normalized_prompt_spec: Dict[str, Any] = {}
    elif isinstance(prompt_spec, Mapping):
        normalized_prompt_spec = dict(prompt_spec)
    else:
        normalized_prompt_spec = {
            "contract": getattr(prompt_spec, "contract", None),
            "artifact_type": "code"
            if str(getattr(prompt_spec, "language", "")).lower() == "python"
            else None,
        }
    normalized_metadata = _prepare_artifact_metadata(
        artifact,
        metadata,
        normalized_prompt_spec,
    )
    normalized_history = list(historical_outputs or [])

    metric_results = [
        check_dependency_hallucination(
            artifact,
            normalized_metadata,
            normalized_prompt_spec,
            normalized_history,
        ),
        check_api_validity(
            artifact,
            normalized_metadata,
            normalized_prompt_spec,
            normalized_history,
        ),
        check_cli_validity(
            artifact,
            normalized_metadata,
            normalized_prompt_spec,
            normalized_history,
        ),
        check_executable_integrity(
            artifact,
            normalized_metadata,
            normalized_prompt_spec,
            normalized_history,
        ),
        check_requirement_consistency(
            artifact,
            normalized_metadata,
            normalized_prompt_spec,
            normalized_history,
        ),
        check_recurrent_hallucination(
            artifact,
            normalized_metadata,
            normalized_prompt_spec,
            normalized_history,
        ),
    ]

    hallucination_flags = _flatten_hallucination_flags(metric_results)
    overall_status = "pass" if all(
        result["status"] == "pass" for result in metric_results
    ) else "fail"

    return {
        "artifact_id": artifact_id,
        "overall_status": overall_status,
        "metric_results": metric_results,
        "hallucination_flags": hallucination_flags,
        "risk_score": _compute_risk_score(metric_results),
    }


if __name__ == "__main__":
    example_artifact = """
import requests
import imaginary_sdk

def main():
    response = requests.fetch_json("https://example.com/data.json")
    print(response)
"""

    example_metadata = {
        "artifact_type": "code",
        "imports": ["requests", "imaginary_sdk", "pandas==9.9.9"],
        "api_calls": [
            {"library": "requests", "symbol": "fetch_json", "kind": "function", "args_count": 1},
            {"library": "pandas", "symbol": "read_csv", "kind": "function", "args_count": 1},
        ],
        "cli_calls": ["git status --json", "foocli --assist"],
    }

    example_prompt_spec = {
        "artifact_type": "code",
        "required_imports": ["requests"],
        "forbidden_terms": ["TODO"],
        "must_define": ["main"],
        "disallow_network": False,
    }

    example_history = [
        {
            "artifact": "import imaginary_sdk\nrequests.fetch_json('https://example.com')",
            "metadata": {
                "artifact_type": "code",
                "imports": ["imaginary_sdk"],
                "api_calls": [{"library": "requests", "symbol": "fetch_json"}],
            },
        }
    ]

    report = run_failure_checks(
        artifact_id="artifact-demo-001",
        artifact=example_artifact,
        metadata=example_metadata,
        prompt_spec=example_prompt_spec,
        historical_outputs=example_history,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
