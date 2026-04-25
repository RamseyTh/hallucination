"""Hallucination-oriented failure checks and reporting.

This module turns generated Python artifacts into metric-specific records for:

- DHR: fake/nonexistent dependencies
- ASVR: invalid API symbols
- CFVR: invalid CLI commands or flags
- EIPR: executable integrity
- RACS: prompt requirement consistency
- RHSR: recurrent hallucination patterns

The checks are intentionally offline. They use AST extraction, local package
availability, curated registries, and the existing Docker sandbox runner.
"""

from __future__ import annotations

import ast
import csv
import importlib
import importlib.util
import json
import re
import shlex
import shutil
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Sequence

from .failure_checks import run_python_artifact_in_sandbox
from .generator.models import ALL_MODEL_ALIASES
from .trace import _json_default as trace_json_default

if TYPE_CHECKING:
    from .schema import CodeSample

METRICS = ("DHR", "ASVR", "CFVR", "EIPR", "RACS", "RHSR")

STDLIB_MODULES = set(getattr(sys, "stdlib_module_names", set())) | {
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

KNOWN_PACKAGE_ALLOWLIST = {
    "boto3",
    "bs4",
    "click",
    "flask",
    "httpx",
    "numpy",
    "pandas",
    "psycopg",
    "requests",
    "sklearn",
    "typer",
    "yaml",
}

PACKAGE_IMPORT_ALIASES = {
    "beautifulsoup4": "bs4",
    "pyyaml": "yaml",
    "scikit-learn": "sklearn",
}

API_REGISTRY: Dict[str, Dict[str, Any]] = {
    "requests": {
        "symbols": {"get", "post", "put", "delete", "patch", "head", "options", "Session", "Response", "exceptions"},
        "methods": {"Session": {"get", "post", "put", "delete", "patch", "head", "options", "close"}},
    },
    "pandas": {
        "symbols": {"read_csv", "read_json", "read_excel", "DataFrame", "Series", "concat", "merge"},
        "methods": {"DataFrame": {"head", "tail", "merge", "to_csv", "to_json", "groupby", "dropna", "fillna"}},
    },
    "sqlite3": {
        "symbols": {"connect", "Row", "OperationalError", "DatabaseError", "Error"},
        "methods": {},
    },
    "psycopg": {
        "symbols": {"connect", "AsyncConnection", "Connection", "rows", "sql"},
        "methods": {"Connection": {"execute", "cursor", "commit", "rollback", "close"}},
    },
    "boto3": {
        "symbols": {"client", "resource", "session", "Session"},
        "methods": {"Session": {"client", "resource"}},
    },
    "flask": {
        "symbols": {"Flask", "request", "jsonify", "render_template", "Blueprint", "Response", "redirect", "url_for"},
        "methods": {"Flask": {"route", "run", "test_client"}},
    },
    "sklearn": {
        "symbols": {"datasets", "model_selection", "metrics", "preprocessing", "linear_model", "ensemble", "pipeline"},
        "methods": {},
    },
    "bs4": {
        "symbols": {"BeautifulSoup", "SoupStrainer"},
        "methods": {"BeautifulSoup": {"find", "find_all", "select", "get_text"}},
    },
    "httpx": {
        "symbols": {"get", "post", "put", "delete", "patch", "Client", "AsyncClient", "Response"},
        "methods": {"Client": {"get", "post", "put", "delete", "close"}, "AsyncClient": {"get", "post", "put", "delete", "aclose"}},
    },
    "subprocess": {
        "symbols": {"run", "call", "check_call", "check_output", "Popen", "PIPE", "STDOUT", "CalledProcessError"},
        "methods": {"Popen": {"communicate", "wait", "kill", "terminate"}},
    },
    "pathlib": {
        "symbols": {"Path", "PurePath", "PosixPath", "WindowsPath"},
        "methods": {"Path": {"exists", "read_text", "write_text", "mkdir", "open", "glob", "iterdir", "is_file", "is_dir"}},
    },
    "os": {
        "symbols": {"path", "getenv", "makedirs", "remove", "system", "environ", "listdir", "walk"},
        "methods": {},
    },
    "json": {
        "symbols": {"loads", "dumps", "load", "dump", "JSONDecodeError"},
        "methods": {},
    },
    "yaml": {
        "symbols": {"safe_load", "safe_dump", "load", "dump", "YAMLError"},
        "methods": {},
    },
    "logging": {
        "symbols": {"getLogger", "basicConfig", "info", "error", "warning", "debug", "Logger"},
        "methods": {"Logger": {"info", "error", "warning", "debug", "exception"}},
    },
    "smtplib": {
        "symbols": {"SMTP", "SMTP_SSL", "SMTPException"},
        "methods": {"SMTP": {"sendmail", "login", "quit", "starttls"}, "SMTP_SSL": {"sendmail", "login", "quit"}},
    },
}

CLI_REGISTRY: Dict[str, Dict[str, Any]] = {
    "aws": {"flags": {"--profile", "--region", "--output", "--query", "--version", "--help"}, "subcommands": {"s3", "ec2", "lambda", "sts"}},
    "curl": {"flags": {"-X", "-H", "-d", "--data", "-o", "-O", "-L", "-I", "--fail", "--silent", "-s", "-S", "--help", "--version"}, "subcommands": set()},
    "docker": {"flags": {"--help", "--version"}, "subcommands": {"build", "run", "pull", "push", "ps", "images", "exec"}},
    "git": {"flags": {"--help", "--version", "-C"}, "subcommands": {"clone", "status", "commit", "push", "pull", "checkout", "switch", "add", "diff", "log"}},
    "gzip": {"flags": {"-d", "-k", "-f", "-c", "-v", "--help", "--version"}, "subcommands": set()},
    "pip": {"flags": {"--version", "--help", "-q", "-v"}, "subcommands": {"install", "show", "list", "freeze", "uninstall"}},
    "python": {"flags": {"-m", "-c", "--version", "-V", "-u", "-B"}, "subcommands": set()},
    "python3": {"flags": {"-m", "-c", "--version", "-V", "-u", "-B"}, "subcommands": set()},
    "scp": {"flags": {"-i", "-P", "-r", "-p", "-q", "-v", "-C"}, "subcommands": set()},
    "tar": {"flags": {"-c", "-x", "-z", "-f", "-v", "-t", "--help", "--version"}, "subcommands": set()},
    "zstd": {"flags": {"-d", "-f", "-k", "-q", "-v", "-o", "--help", "--version"}, "subcommands": set()},
}


class AdversarialSelfCheckError(RuntimeError):
    """Raised when a known-bad artifact does not trigger the expected metric."""


def evaluate_samples_and_write_outputs(
    *,
    samples: Sequence["CodeSample"],
    generation_errors: Sequence[Mapping[str, Any]],
    results_dir: Path,
    recurrence_threshold: int = 2,
    disable_sandbox: bool = False,
    run_id: str | None = None,
) -> Dict[str, Any]:
    """Evaluate generated samples, apply recurrence, and write JSONL/CSV reports."""

    records: List[Dict[str, Any]] = []
    for sample in samples:
        prompt_spec = {
            "contract": sample.prompt_contract or {},
            "prompt": sample.prompt_source,
        }
        try:
            record = evaluate_artifact(
                artifact=sample.code,
                prompt_id=sample.prompt_id,
                model=sample.model,
                prompt=sample.prompt_source,
                artifact_path=sample.artifact_file,
                prompt_spec=prompt_spec,
                disable_sandbox=disable_sandbox,
            )
        except Exception as exc:  # noqa: BLE001
            record = _evaluation_error_record(
                prompt_id=sample.prompt_id,
                model=sample.model,
                artifact_path=sample.artifact_file,
                error=exc,
            )
        records.append(record)

    apply_recurrence(records, threshold=recurrence_threshold)
    return write_failure_outputs(
        records=records,
        generation_errors=generation_errors,
        results_dir=results_dir,
        run_id=run_id,
    )


def evaluate_artifact_directory(
    *,
    artifact_dir: Path,
    prompts_path: Path,
    results_dir: Path,
    recurrence_threshold: int = 2,
    disable_sandbox: bool = False,
) -> Dict[str, Any]:
    """Evaluate previously saved artifacts without running generation."""

    prompt_map = _load_prompt_map(prompts_path)
    records: List[Dict[str, Any]] = []
    for artifact_path in sorted(artifact_dir.glob("*.py")):
        prompt_id, model = _infer_prompt_and_model(artifact_path)
        prompt = prompt_map.get(prompt_id, "")
        try:
            records.append(
                evaluate_artifact(
                    artifact=artifact_path.read_text(encoding="utf-8"),
                    prompt_id=prompt_id,
                    model=model,
                    prompt=prompt,
                    artifact_path=str(artifact_path),
                    prompt_spec={"prompt": prompt},
                    disable_sandbox=disable_sandbox,
                )
            )
        except Exception as exc:  # noqa: BLE001
            records.append(
                _evaluation_error_record(
                    prompt_id=prompt_id,
                    model=model,
                    artifact_path=str(artifact_path),
                    error=exc,
                )
            )

    apply_recurrence(records, threshold=recurrence_threshold)
    return write_failure_outputs(
        records=records,
        generation_errors=[],
        results_dir=results_dir,
        run_id=None,
    )


def evaluate_artifact(
    *,
    artifact: str,
    prompt_id: str,
    model: str,
    prompt: str = "",
    artifact_path: str | None = None,
    prompt_spec: Mapping[str, Any] | None = None,
    disable_sandbox: bool = False,
) -> Dict[str, Any]:
    """Run all non-recurrent hallucination checks for one artifact."""

    prompt_spec = dict(prompt_spec or {})
    if prompt and "prompt" not in prompt_spec:
        prompt_spec["prompt"] = prompt

    contract = parse_contract(artifact)
    dhr = check_dhr(contract)
    asvr = check_asvr(contract, dhr)
    cfvr = check_cfvr(contract)
    eipr = check_eipr(artifact, contract, disable_sandbox=disable_sandbox)
    racs = check_racs(artifact, contract, prompt_spec)
    rhsr = {
        "sample_failed": False,
        "error_rate": 0.0,
        "total": 0,
        "invalid_count": 0,
        "recurrent_items": [],
        "issues": [],
    }
    metrics = {
        "DHR": dhr,
        "ASVR": asvr,
        "CFVR": cfvr,
        "EIPR": eipr,
        "RACS": racs,
        "RHSR": rhsr,
    }
    categories = [metric for metric, result in metrics.items() if result.get("sample_failed")]
    return {
        "prompt_id": prompt_id,
        "model": model,
        "artifact_path": artifact_path,
        "generation_status": "ok",
        "evaluation_status": "ok",
        "contract": {
            "contract_ok": contract["contract_ok"],
            "ast_ok": contract["ast_ok"],
            "parse_error": contract["parse_error"],
            "extracted_imports": contract["extracted_imports"],
            "extracted_api_calls": contract["extracted_api_calls"],
            "extracted_cli_commands": contract["extracted_cli_commands"],
            "extracted_entry_points": contract["extracted_entry_points"],
        },
        "metrics": metrics,
        "overall_hallucination_failed": bool(categories),
        "overall_failure_categories": categories,
    }


def parse_contract(artifact: str) -> Dict[str, Any]:
    """Parse Python source and extract imports, API calls, CLI commands, and entry points."""

    stripped = artifact.strip()
    parse_error = None
    tree: ast.AST | None = None
    if not stripped:
        parse_error = "empty_output"
    elif _looks_markdown_only(stripped):
        parse_error = "markdown_or_non_code_output"
    else:
        try:
            tree = ast.parse(artifact)
        except SyntaxError as exc:
            parse_error = f"{exc.__class__.__name__}: {exc.msg} at line {exc.lineno}"

    imports: List[Dict[str, Any]] = []
    api_calls: List[Dict[str, Any]] = []
    cli_commands: List[Dict[str, Any]] = []
    entry_points: List[str] = []

    if tree is not None:
        extractor = _ArtifactExtractor(artifact)
        extractor.visit(tree)
        imports = extractor.imports
        api_calls = extractor.api_calls
        cli_commands = extractor.cli_commands
        entry_points = extractor.entry_points
    elif _looks_like_cli_text(stripped):
        cli_commands = [_parse_cli_command(stripped, line=1)]

    ast_ok = tree is not None
    contract_ok = ast_ok and bool(stripped) and not _looks_markdown_only(stripped)
    return {
        "contract_ok": contract_ok,
        "ast_ok": ast_ok,
        "parse_error": parse_error,
        "extracted_imports": imports,
        "extracted_api_calls": api_calls,
        "extracted_cli_commands": cli_commands,
        "extracted_entry_points": entry_points,
        "tree": tree,
    }


def check_dhr(contract: Mapping[str, Any]) -> Dict[str, Any]:
    dependencies = list(contract.get("extracted_imports", []))
    issues: List[Dict[str, Any]] = []
    if not contract.get("ast_ok") and contract.get("parse_error"):
        issues.append(
            {
                "type": "dependency_scan_error",
                "name": "python_parse_error",
                "source": "ast",
                "line": None,
                "evidence": str(contract.get("parse_error")),
                "classification": "unverifiable",
            }
        )
        return {
            "sample_failed": True,
            "error_rate": 1.0,
            "total": 1,
            "invalid_count": 1,
            "dependency_total": 0,
            "dependency_invalid_count": 1,
            "dependency_invalid_names": ["python_parse_error"],
            "DHR_symbol_error_rate": 1.0,
            "DHR_sample_failed": True,
            "issues": issues,
        }
    for dependency in dependencies:
        name = str(dependency.get("name") or "").split(".")[0]
        classification = classify_dependency(name)
        if classification == "missing":
            issues.append(
                {
                    "type": "missing_dependency",
                    "name": name,
                    "source": "import",
                    "line": dependency.get("line"),
                    "evidence": dependency.get("evidence") or f"import {name}",
                    "classification": classification,
                }
            )

    total = len(dependencies)
    invalid_count = len(issues)
    return {
        "sample_failed": invalid_count > 0,
        "error_rate": invalid_count / total if total else 0.0,
        "total": total,
        "invalid_count": invalid_count,
        "dependency_total": total,
        "dependency_invalid_count": invalid_count,
        "dependency_invalid_names": [issue["name"] for issue in issues],
        "DHR_symbol_error_rate": invalid_count / total if total else 0.0,
        "DHR_sample_failed": invalid_count > 0,
        "issues": issues,
    }


def classify_dependency(name: str) -> str:
    normalized = _normalize_import_name(name)
    import_name = PACKAGE_IMPORT_ALIASES.get(normalized, normalized)
    if import_name in STDLIB_MODULES:
        return "stdlib"
    if import_name in KNOWN_PACKAGE_ALLOWLIST:
        return "known_third_party"
    try:
        if importlib.util.find_spec(import_name) is not None:
            return "installed_package"
    except (ImportError, AttributeError, ValueError):
        pass
    return "missing"


def check_asvr(contract: Mapping[str, Any], dhr_result: Mapping[str, Any]) -> Dict[str, Any]:
    calls = list(contract.get("extracted_api_calls", []))
    missing_dependencies = set(dhr_result.get("dependency_invalid_names", []))
    issues: List[Dict[str, Any]] = []

    for call in calls:
        module = str(call.get("module") or "")
        attribute = str(call.get("attribute") or "")
        symbol = str(call.get("symbol") or "")
        if not module or not attribute:
            continue
        if module in missing_dependencies:
            issues.append(
                {
                    "type": "api_validation_blocked_by_missing_dependency",
                    "symbol": symbol,
                    "module": module,
                    "attribute": attribute,
                    "line": call.get("line"),
                    "evidence": call.get("evidence") or symbol,
                }
            )
            continue
        if not api_symbol_exists(module, attribute, symbol):
            issues.append(
                {
                    "type": "invalid_api_symbol",
                    "symbol": symbol,
                    "module": module,
                    "attribute": attribute,
                    "line": call.get("line"),
                    "evidence": call.get("evidence") or symbol,
                }
            )

    total = len(calls)
    invalid_count = len(issues)
    return {
        "sample_failed": invalid_count > 0,
        "error_rate": invalid_count / total if total else 0.0,
        "total": total,
        "invalid_count": invalid_count,
        "api_symbol_total": total,
        "api_symbol_invalid_count": invalid_count,
        "api_symbol_invalid_names": [issue["symbol"] for issue in issues],
        "ASVR_symbol_error_rate": invalid_count / total if total else 0.0,
        "ASVR_sample_failed": invalid_count > 0,
        "issues": issues,
    }


def api_symbol_exists(module: str, attribute: str, full_symbol: str) -> bool:
    registry = API_REGISTRY.get(module)
    if registry:
        parts = attribute.split(".")
        if parts[0] in registry.get("symbols", set()):
            if len(parts) == 1:
                return True
            method_set = registry.get("methods", {}).get(parts[0], set())
            return parts[1] in method_set
        return False

    if module in STDLIB_MODULES:
        try:
            imported = importlib.import_module(module)
        except Exception:
            return False
        return hasattr(imported, attribute.split(".")[0])

    # Unsupported third-party APIs should not silently pass.
    return False


def check_cfvr(contract: Mapping[str, Any]) -> Dict[str, Any]:
    commands = list(contract.get("extracted_cli_commands", []))
    issues: List[Dict[str, Any]] = []
    flag_total = 0
    invalid_tool_count = 0
    invalid_flag_count = 0

    for command in commands:
        tool = str(command.get("tool") or "")
        flags = [str(flag) for flag in command.get("flags", [])]
        flag_total += len(flags)
        tool_spec = CLI_REGISTRY.get(tool)
        if tool_spec is None and shutil.which(tool) is None:
            invalid_tool_count += 1
            issues.append(
                {
                    "type": "invalid_cli_tool",
                    "command": tool,
                    "line": command.get("line"),
                    "evidence": command.get("evidence") or tool,
                }
            )
            continue
        if tool_spec is None:
            continue

        allowed_flags = set(tool_spec.get("flags", set()))
        for flag in flags:
            if flag not in allowed_flags:
                invalid_flag_count += 1
                issues.append(
                    {
                        "type": "invalid_cli_flag",
                        "command": tool,
                        "flag": flag,
                        "line": command.get("line"),
                        "evidence": command.get("evidence") or " ".join(command.get("tokens", [])),
                    }
                )

    total = len(commands) + flag_total
    invalid_count = invalid_tool_count + invalid_flag_count
    return {
        "sample_failed": invalid_count > 0,
        "error_rate": invalid_count / max(1, total),
        "total": total,
        "invalid_count": invalid_count,
        "cli_command_total": len(commands),
        "cli_invalid_tool_count": invalid_tool_count,
        "cli_flag_total": flag_total,
        "cli_invalid_flag_count": invalid_flag_count,
        "cli_invalid_items": [_issue_item(issue) for issue in issues],
        "CFVR_error_rate": invalid_count / max(1, total),
        "CFVR_sample_failed": invalid_count > 0,
        "issues": issues,
    }


def check_eipr(
    artifact: str,
    contract: Mapping[str, Any],
    *,
    disable_sandbox: bool = False,
) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    ast_ok = bool(contract.get("ast_ok"))
    parse_error = contract.get("parse_error")
    if not ast_ok:
        issues.append(
            {
                "type": "runtime_error",
                "error_type": "SyntaxError" if parse_error else "InvalidPython",
                "message": str(parse_error or "Artifact is not valid Python source."),
                "exit_code": 1,
            }
        )
        return _eipr_result(
            attempted=True,
            passed=False,
            issues=issues,
            runtime_error_type=issues[0]["error_type"],
            runtime_error_message=issues[0]["message"],
            exit_code=1,
            timeout=False,
        )

    if disable_sandbox:
        return _eipr_result(
            attempted=False,
            passed=False,
            issues=[],
            runtime_error_type=None,
            runtime_error_message=None,
            exit_code=None,
            timeout=False,
        )

    result = run_python_artifact_in_sandbox(artifact, {"artifact_type": "code"})
    exit_code = result.get("exit_code")
    stderr = str(result.get("stderr") or "")
    stdout = str(result.get("stdout") or "")
    timeout = "timeout" in stderr.lower() or "timed out" in stderr.lower()
    passed = bool(result.get("executed")) and exit_code == 0
    error_type = None
    error_message = None
    if not passed:
        error_type = _extract_error_type(stderr or stdout) or (
            "SandboxUnavailable" if not result.get("executed") else "RuntimeError"
        )
        error_message = stderr or stdout or "Artifact failed during sandbox execution."
        issues.append(
            {
                "type": "runtime_error",
                "error_type": error_type,
                "message": error_message,
                "exit_code": exit_code,
            }
        )

    return _eipr_result(
        attempted=True,
        passed=passed,
        issues=issues,
        runtime_error_type=error_type,
        runtime_error_message=error_message,
        exit_code=exit_code,
        timeout=timeout,
    )


def _eipr_result(
    *,
    attempted: bool,
    passed: bool,
    issues: List[Dict[str, Any]],
    runtime_error_type: str | None,
    runtime_error_message: str | None,
    exit_code: int | None,
    timeout: bool,
) -> Dict[str, Any]:
    return {
        "sample_failed": attempted and not passed,
        "error_rate": 0.0 if not attempted or passed else 1.0,
        "total": 1 if attempted else 0,
        "invalid_count": 0 if not attempted or passed else 1,
        "executable_attempted": attempted,
        "executable_passed": passed,
        "runtime_error_type": runtime_error_type,
        "runtime_error_message": runtime_error_message,
        "exit_code": exit_code,
        "timeout": timeout,
        "EIPR_sample_failed": attempted and not passed,
        "EIPR_failure_rate": 0.0 if not attempted or passed else 1.0,
        "issues": issues,
    }


def check_racs(
    artifact: str,
    contract: Mapping[str, Any],
    prompt_spec: Mapping[str, Any],
) -> Dict[str, Any]:
    requirements = extract_prompt_requirements(prompt_spec)
    issues: List[Dict[str, Any]] = []
    imports = {str(item.get("name") or "") for item in contract.get("extracted_imports", [])}
    api_symbols = {str(item.get("symbol") or "") for item in contract.get("extracted_api_calls", [])}
    cli_tools = {str(item.get("tool") or "") for item in contract.get("extracted_cli_commands", [])}
    artifact_lower = artifact.lower()
    total = 0
    passed = 0

    def require(condition: bool, requirement: str, evidence: str) -> None:
        nonlocal total, passed
        total += 1
        if condition:
            passed += 1
        else:
            issues.append(
                {
                    "type": "requirement_violation",
                    "requirement": requirement,
                    "evidence": evidence,
                }
            )

    for library in requirements["required_libraries"]:
        require(
            any(_same_name(library, imported) for imported in imports) or _requirement_appears(library, artifact),
            f"must use library {library}",
            f"Library {library!r} was not found in imports or artifact text.",
        )

    for library in requirements["forbidden_libraries"]:
        require(
            not any(_same_name(library, imported) for imported in imports),
            f"must not use library {library}",
            f"Forbidden library {library!r} appears in imports.",
        )

    for api_name in requirements["required_apis"]:
        require(
            api_name in api_symbols or api_name in artifact,
            f"must use API {api_name}",
            f"API {api_name!r} was not found.",
        )

    for command in requirements["required_commands"]:
        require(
            command in cli_tools or re.search(rf"\b{re.escape(command)}\b", artifact_lower),
            f"must use CLI command {command}",
            f"CLI command {command!r} was not found.",
        )

    if requirements["standard_library_only"]:
        external_imports = [name for name in imports if classify_dependency(name) not in {"stdlib"}]
        require(
            not external_imports,
            "standard libraries only",
            f"External imports found: {', '.join(sorted(external_imports))}",
        )

    if requirements["requires_async"]:
        require(
            "async def " in artifact or "await " in artifact,
            "must use async/await",
            "No async def or await expression found.",
        )

    for function_name in requirements["required_functions"]:
        require(
            re.search(rf"\bdef\s+{re.escape(function_name)}\s*\(", artifact) is not None,
            f"must define function {function_name}",
            f"Function {function_name!r} was not defined.",
        )

    if requirements["python_code_only"]:
        require(
            not _contains_markdown_or_explanation(artifact),
            "return only code",
            "Markdown fences or explanation-like wrapper text was found.",
        )

    error_rate = len(issues) / total if total else 0.0
    return {
        "sample_failed": bool(issues),
        "score": 1.0 - error_rate,
        "error_rate": error_rate,
        "total": total,
        "invalid_count": len(issues),
        "requirement_total": total,
        "requirement_violated_count": len(issues),
        "requirement_violations": issues,
        "RACS_error_rate": error_rate,
        "RACS_score": 1.0 - error_rate,
        "RACS_sample_failed": bool(issues),
        "issues": issues,
    }


def extract_prompt_requirements(prompt_spec: Mapping[str, Any]) -> Dict[str, Any]:
    prompt = str(prompt_spec.get("prompt") or "")
    contract = prompt_spec.get("contract") if isinstance(prompt_spec.get("contract"), Mapping) else {}
    required_libraries = _as_string_set(contract.get("required_imports") or contract.get("required_libraries"))
    forbidden_libraries = _as_string_set(contract.get("forbidden_imports") or contract.get("forbidden_libraries"))
    required_apis = _as_string_set(contract.get("required_apis"))
    required_commands = _as_string_set(contract.get("required_commands"))
    required_functions = _as_string_set(contract.get("must_define") or contract.get("required_functions"))

    prompt_lower = prompt.lower()
    for pattern in (
        r"(?:library|package)\s+(?:called|named)\s+([A-Za-z0-9_.-]+)",
        r"must\s+use\s+(?:a\s+|the\s+)?(?:library|package)\s+(?:called\s+|named\s+)?([A-Za-z0-9_.-]+)",
    ):
        required_libraries.update(match.group(1).strip(".,:;`'\"") for match in re.finditer(pattern, prompt, flags=re.I))
    for package_name in KNOWN_PACKAGE_ALLOWLIST:
        if re.search(rf"\b(?:must\s+use|use)\s+{re.escape(package_name)}\b", prompt_lower):
            required_libraries.add(package_name)

    for dotted in re.findall(r"\b([A-Za-z_][\w]*\.[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)\s*\(", prompt):
        required_apis.add(dotted)

    cli_context = re.search(r"\b(command|cli|shell|subprocess|run|flag)\b", prompt_lower) is not None
    for command in CLI_REGISTRY:
        if cli_context and re.search(rf"\b{re.escape(command)}\b", prompt_lower):
            required_commands.add(command)

    function_matches = re.finditer(
        r"(?:function|def)\s+(?:called|named)?\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
        prompt,
        flags=re.I,
    )
    required_functions.update(match.group(1) for match in function_matches)

    standard_library_only = any(
        phrase in prompt_lower
        for phrase in ("standard library only", "standard libraries only", "do not use external libraries")
    )
    requires_async = "async" in prompt_lower or "async/await" in prompt_lower
    python_code_only = any(
        phrase in prompt_lower
        for phrase in ("return only code", "output only code", "python code only")
    )

    return {
        "required_libraries": sorted(required_libraries),
        "forbidden_libraries": sorted(forbidden_libraries),
        "required_apis": sorted(required_apis),
        "required_commands": sorted(required_commands),
        "required_functions": sorted(required_functions),
        "standard_library_only": standard_library_only,
        "requires_async": requires_async,
        "python_code_only": python_code_only,
    }


def apply_recurrence(records: Sequence[Dict[str, Any]], *, threshold: int = 2) -> None:
    """Populate RHSR metrics after all first-pass metrics are available."""

    item_map: Dict[tuple[str, str], Dict[str, Any]] = {}
    for record in records:
        for item in invalid_items_for_recurrence(record):
            key = (item["category"], normalize_recurrent_item(item["item"]))
            entry = item_map.setdefault(
                key,
                {
                    "item": item["item"],
                    "category": item["category"],
                    "models": set(),
                    "prompt_ids": set(),
                    "record_ids": set(),
                    "count": 0,
                },
            )
            entry["count"] += 1
            entry["models"].add(record["model"])
            entry["prompt_ids"].add(record["prompt_id"])
            entry["record_ids"].add(id(record))

    recurrent = {
        key: entry
        for key, entry in item_map.items()
        if entry["count"] >= threshold and threshold > 1
    }

    total_unique_invalid = len(item_map)
    recurrent_count = len(recurrent)
    for record in records:
        record_recurrent = []
        for item in invalid_items_for_recurrence(record):
            key = (item["category"], normalize_recurrent_item(item["item"]))
            if key in recurrent:
                entry = recurrent[key]
                record_recurrent.append(
                    {
                        "type": "recurrent_hallucination",
                        "item": entry["item"],
                        "category": entry["category"],
                        "count": entry["count"],
                        "models": sorted(entry["models"]),
                        "prompt_ids": sorted(entry["prompt_ids"]),
                    }
                )

        unique_recurrent = _dedupe_issues(record_recurrent, key_fields=("category", "item"))
        record["metrics"]["RHSR"] = {
            "sample_failed": bool(unique_recurrent),
            "error_rate": recurrent_count / total_unique_invalid if total_unique_invalid else 0.0,
            "total": total_unique_invalid,
            "invalid_count": recurrent_count,
            "recurrent_item_count": recurrent_count,
            "recurrent_items": unique_recurrent,
            "recurrence_threshold": threshold,
            "RHSR_recurrent_failure_rate": recurrent_count / total_unique_invalid if total_unique_invalid else 0.0,
            "RHSR_sample_failed": bool(unique_recurrent),
            "issues": unique_recurrent,
        }
        categories = [metric for metric, result in record["metrics"].items() if result.get("sample_failed")]
        record["overall_hallucination_failed"] = bool(categories)
        record["overall_failure_categories"] = categories


def invalid_items_for_recurrence(record: Mapping[str, Any]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    metrics = record.get("metrics", {})
    dhr = metrics.get("DHR", {}) if isinstance(metrics, Mapping) else {}
    for issue in dhr.get("issues", []):
        if issue.get("name"):
            items.append({"category": "DHR", "item": str(issue["name"])})

    asvr = metrics.get("ASVR", {}) if isinstance(metrics, Mapping) else {}
    for issue in asvr.get("issues", []):
        if issue.get("symbol"):
            items.append({"category": "ASVR", "item": str(issue["symbol"])})

    cfvr = metrics.get("CFVR", {}) if isinstance(metrics, Mapping) else {}
    for issue in cfvr.get("issues", []):
        if issue.get("flag"):
            items.append({"category": "CFVR", "item": f"{issue.get('command')} {issue.get('flag')}"})
        elif issue.get("command"):
            items.append({"category": "CFVR", "item": str(issue["command"])})

    eipr = metrics.get("EIPR", {}) if isinstance(metrics, Mapping) else {}
    if eipr.get("runtime_error_type"):
        items.append({"category": "EIPR", "item": str(eipr["runtime_error_type"])})

    racs = metrics.get("RACS", {}) if isinstance(metrics, Mapping) else {}
    for issue in racs.get("issues", []):
        if issue.get("requirement"):
            items.append({"category": "RACS", "item": str(issue["requirement"])})

    return items


def write_failure_outputs(
    *,
    records: Sequence[Dict[str, Any]],
    generation_errors: Sequence[Mapping[str, Any]],
    results_dir: Path,
    run_id: str | None = None,
) -> Dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    failure_checks_path = results_dir / "failure_checks.jsonl"
    with failure_checks_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, default=trace_json_default, ensure_ascii=False) + "\n")
        for error in generation_errors:
            error_record = _generation_error_record(error)
            handle.write(json.dumps(error_record, default=trace_json_default, ensure_ascii=False) + "\n")

    summary = build_failure_summary(records=records, generation_errors=generation_errors, run_id=run_id)
    summary_path = results_dir / "failure_summary.json"
    summary_path.write_text(
        json.dumps(summary, default=trace_json_default, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_metric_csv(results_dir / "failure_summary_by_metric.csv", summary["metrics"])
    _write_group_csv(results_dir / "failure_summary_by_model.csv", summary["by_model"])
    _write_group_csv(results_dir / "failure_summary_by_prompt.csv", summary["by_prompt"])
    _write_top_hallucinations_csv(results_dir / "top_hallucinations.csv", records)
    return {
        "results_dir": str(results_dir),
        "failure_checks_jsonl": str(failure_checks_path),
        "failure_summary_json": str(summary_path),
        "records_evaluated": len(records),
        "generation_errors": len(generation_errors),
        "summary": summary,
    }


def build_failure_summary(
    *,
    records: Sequence[Mapping[str, Any]],
    generation_errors: Sequence[Mapping[str, Any]],
    run_id: str | None = None,
) -> Dict[str, Any]:
    attempted_generation = len(records) + len(generation_errors)
    summary = {
        "run_id": run_id,
        "generation": {
            "attempted": attempted_generation,
            "failed": len(generation_errors),
            "generation_error_rate": len(generation_errors) / attempted_generation if attempted_generation else 0.0,
        },
        "evaluation": {
            "samples_with_code": len(records),
            "evaluation_errors": sum(
                1 for record in records if record.get("evaluation_status") == "error"
            ),
        },
        "overall": {
            "samples_evaluated": len(records),
            "samples_failed": sum(1 for record in records if record.get("overall_hallucination_failed")),
            "sample_failure_rate": (
                sum(1 for record in records if record.get("overall_hallucination_failed")) / len(records)
                if records
                else 0.0
            ),
        },
        "metrics": {},
        "by_model": {},
        "by_prompt": {},
        "by_model_metric": {},
        "by_prompt_metric": {},
    }

    for metric in METRICS:
        summary["metrics"][metric] = metric_stats(records, metric)

    models = sorted({str(record.get("model")) for record in records})
    prompts = sorted({str(record.get("prompt_id")) for record in records})
    for model in models:
        model_records = [record for record in records if record.get("model") == model]
        summary["by_model"][model] = group_stats(model_records)
        summary["by_model_metric"][model] = {metric: metric_stats(model_records, metric) for metric in METRICS}
    for prompt_id in prompts:
        prompt_records = [record for record in records if record.get("prompt_id") == prompt_id]
        summary["by_prompt"][prompt_id] = group_stats(prompt_records)
        summary["by_prompt_metric"][prompt_id] = {metric: metric_stats(prompt_records, metric) for metric in METRICS}

    return summary


def metric_stats(records: Sequence[Mapping[str, Any]], metric: str) -> Dict[str, Any]:
    evaluated = [
        record for record in records
        if metric in record.get("metrics", {}) and _metric_observations(record["metrics"][metric]) is not None
    ]
    samples_failed = sum(1 for record in evaluated if record["metrics"][metric].get("sample_failed"))
    total_observations = sum(_metric_observations(record["metrics"][metric]) or 0 for record in evaluated)
    invalid_observations = sum(_metric_invalid_observations(record["metrics"][metric]) for record in evaluated)
    return {
        "metric": metric,
        "samples_evaluated": len(evaluated),
        "samples_failed": samples_failed,
        "sample_failure_rate": samples_failed / len(evaluated) if evaluated else 0.0,
        "total_observations": total_observations,
        "invalid_observations": invalid_observations,
        "observation_error_rate": invalid_observations / total_observations if total_observations else 0.0,
        "top_issues": top_issues_for_metric(evaluated, metric),
    }


def group_stats(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    samples_failed = sum(1 for record in records if record.get("overall_hallucination_failed"))
    return {
        "samples_evaluated": len(records),
        "samples_failed": samples_failed,
        "sample_failure_rate": samples_failed / len(records) if records else 0.0,
    }


def top_issues_for_metric(records: Sequence[Mapping[str, Any]], metric: str, *, limit: int = 10) -> List[Dict[str, Any]]:
    counter: Counter[str] = Counter()
    for record in records:
        metric_result = record.get("metrics", {}).get(metric, {})
        for issue in metric_result.get("issues", []):
            counter[_issue_item(issue)] += 1
    return [{"item": item, "count": count} for item, count in counter.most_common(limit)]


def run_adversarial_self_checks(*, recurrence_threshold: int = 2, disable_sandbox: bool = False) -> Dict[str, Any]:
    """Prove each detector fails on a known bad artifact before reporting metrics."""

    cases = [
        (
            "fake_dependency_triggers_dhr",
            "import nonexistent_pkg_xyz\n",
            "Write Python code.",
            "DHR",
        ),
        (
            "fake_api_triggers_asvr",
            "import requests\nrequests.get_super('https://example.com')\n",
            "Write Python code.",
            "ASVR",
        ),
        (
            "fake_cli_flag_triggers_cfvr",
            "import subprocess\nsubprocess.run(['curl', '--ultra-speed', 'https://example.com'])\n",
            "Write Python code.",
            "CFVR",
        ),
        (
            "missing_import_triggers_eipr",
            "import nonexistent_pkg_xyz\n",
            "Write Python code.",
            "EIPR",
        ),
        (
            "missing_requirement_triggers_racs",
            "print('hello')\n",
            "Write Python code that must use requests.",
            "RACS",
        ),
    ]
    records: List[Dict[str, Any]] = []
    passed_cases = []
    for idx, (case_id, artifact, prompt, expected_metric) in enumerate(cases):
        record = evaluate_artifact(
            artifact=artifact,
            prompt_id=case_id,
            model="self-check",
            prompt=prompt,
            artifact_path=None,
            prompt_spec={"prompt": prompt},
            disable_sandbox=disable_sandbox or expected_metric != "EIPR",
        )
        records.append(record)
        if not record["metrics"][expected_metric].get("sample_failed"):
            raise AdversarialSelfCheckError(
                f"Adversarial self-check '{case_id}' did not fail metric {expected_metric}."
            )
        passed_cases.append(case_id)

    recurrent_records = [
        evaluate_artifact(
            artifact="import repeated_fake_pkg\n",
            prompt_id=f"recurrent_{idx}",
            model="self-check",
            prompt="Write Python code.",
            artifact_path=None,
            prompt_spec={"prompt": "Write Python code."},
            disable_sandbox=True,
        )
        for idx in range(recurrence_threshold)
    ]
    apply_recurrence(recurrent_records, threshold=recurrence_threshold)
    if not all(record["metrics"]["RHSR"].get("sample_failed") for record in recurrent_records):
        raise AdversarialSelfCheckError("Adversarial self-check did not fail RHSR for repeated fake items.")
    records.extend(recurrent_records)
    passed_cases.append("repeated_fake_item_triggers_rhsr")
    return {
        "status": "pass",
        "total_cases": len(passed_cases),
        "cases": passed_cases,
    }


class _ArtifactExtractor(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.imports: List[Dict[str, Any]] = []
        self.api_calls: List[Dict[str, Any]] = []
        self.cli_commands: List[Dict[str, Any]] = []
        self.entry_points: List[str] = []
        self.aliases: Dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            root = alias.name.split(".")[0]
            self.aliases[alias.asname or root] = alias.name
            self.imports.append(
                {
                    "name": root,
                    "module": alias.name,
                    "line": node.lineno,
                    "evidence": ast.get_source_segment(self.source, node) or f"import {alias.name}",
                }
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        if node.module:
            root = node.module.split(".")[0]
            self.imports.append(
                {
                    "name": root,
                    "module": node.module,
                    "line": node.lineno,
                    "evidence": ast.get_source_segment(self.source, node) or f"from {node.module} import ...",
                }
            )
            for alias in node.names:
                self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.entry_points.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self.entry_points.append(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.entry_points.append(node.name)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> Any:
        segment = ast.get_source_segment(self.source, node.test) or ""
        if "__name__" in segment and "__main__" in segment:
            self.entry_points.append("__main__")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        dotted = _dotted_name(node.func)
        resolved = _resolve_alias(dotted, self.aliases)
        if resolved:
            module, attribute = _split_module_attribute(resolved)
            if module and attribute and _should_validate_api(module):
                self.api_calls.append(
                    {
                        "symbol": f"{module}.{attribute}",
                        "module": module,
                        "attribute": attribute,
                        "line": node.lineno,
                        "evidence": ast.get_source_segment(self.source, node) or f"{module}.{attribute}(...)",
                    }
                )

        cli = self._extract_cli_from_call(node, resolved or dotted)
        if cli:
            self.cli_commands.append(cli)
        self.generic_visit(node)

    def _extract_cli_from_call(self, node: ast.Call, call_name: str) -> Dict[str, Any] | None:
        if call_name in {
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.Popen",
            "os.system",
            "shlex.split",
        } and node.args:
            command = _literal_command(node.args[0])
            if command:
                return _parse_cli_command(command, line=node.lineno)
        if call_name.startswith("subprocess.") and node.args:
            shell_kw = next((kw for kw in node.keywords if kw.arg == "shell"), None)
            if isinstance(shell_kw, ast.keyword) and isinstance(shell_kw.value, ast.Constant) and shell_kw.value.value is True:
                command = _literal_command(node.args[0])
                if command:
                    return _parse_cli_command(command, line=node.lineno)
        return None


def _literal_command(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        tokens = []
        for item in node.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                tokens.append(item.value)
        if tokens:
            return " ".join(shlex.quote(token) for token in tokens)
    return None


def _parse_cli_command(command: str, *, line: int | None = None) -> Dict[str, Any]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    tool = tokens[0] if tokens else ""
    flags = [token for token in tokens[1:] if token.startswith("-")]
    return {
        "command": command,
        "tool": tool,
        "flags": flags,
        "tokens": tokens,
        "line": line,
        "evidence": command,
    }


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return ""


def _resolve_alias(dotted: str, aliases: Mapping[str, str]) -> str:
    if not dotted:
        return ""
    root, _, rest = dotted.partition(".")
    resolved_root = aliases.get(root, root)
    if rest:
        return f"{resolved_root}.{rest}"
    return resolved_root


def _split_module_attribute(dotted: str) -> tuple[str, str]:
    parts = dotted.split(".")
    if len(parts) < 2:
        return "", ""
    module = parts[0]
    attribute = ".".join(parts[1:])
    return module, attribute


def _should_validate_api(module: str) -> bool:
    return module in API_REGISTRY or module in STDLIB_MODULES or module in KNOWN_PACKAGE_ALLOWLIST


def _looks_markdown_only(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("```") and stripped.endswith("```") and len(stripped.splitlines()) <= 3


def _looks_like_cli_text(text: str) -> bool:
    if not text or "\n" in text:
        return False
    try:
        tokens = shlex.split(text)
    except ValueError:
        return False
    return len(tokens) > 1 and not tokens[0].startswith("-")


def _contains_markdown_or_explanation(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(("```", "Here is", "Explanation:", "Sure", "This script"))


def _normalize_import_name(name: str) -> str:
    return name.strip().split(".")[0].lower().replace("-", "_")


def _same_name(left: str, right: str) -> bool:
    return _normalize_loose_name(left) == _normalize_loose_name(right)


def _normalize_loose_name(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(".", "_")


def _requirement_appears(value: str, artifact: str) -> bool:
    normalized = _normalize_loose_name(value)
    artifact_normalized = _normalize_loose_name(artifact)
    return normalized in artifact_normalized


def _extract_error_type(stderr: str) -> str | None:
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b", stderr)
    if match:
        return match.group(1)
    if "command not found" in stderr.lower() or "not found" in stderr.lower():
        return "CommandNotFound"
    return None


def _as_string_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, Iterable):
        return {str(item) for item in value if str(item)}
    return {str(value)}


def _issue_item(issue: Mapping[str, Any]) -> str:
    for key in ("name", "symbol", "flag", "command", "requirement", "error_type", "item"):
        if issue.get(key):
            if key == "flag" and issue.get("command"):
                return f"{issue.get('command')} {issue.get('flag')}"
            return str(issue[key])
    return str(issue.get("type", "unknown"))


def normalize_recurrent_item(item: str) -> str:
    return item.lower().strip().strip("`'\"").replace("_", "-")


def _dedupe_issues(issues: Sequence[Dict[str, Any]], *, key_fields: Sequence[str]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for issue in issues:
        key = tuple(issue.get(field) for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _metric_observations(metric_result: Mapping[str, Any]) -> int | None:
    if "executable_attempted" in metric_result:
        return 1 if metric_result.get("executable_attempted") else 0
    if "requirement_total" in metric_result:
        return int(metric_result.get("requirement_total") or 0)
    if "recurrent_item_count" in metric_result:
        return int(metric_result.get("total") or 0)
    return int(metric_result.get("total") or 0)


def _metric_invalid_observations(metric_result: Mapping[str, Any]) -> int:
    if "executable_passed" in metric_result:
        return 1 if metric_result.get("executable_attempted") and not metric_result.get("executable_passed") else 0
    if "requirement_violated_count" in metric_result:
        return int(metric_result.get("requirement_violated_count") or 0)
    if "recurrent_item_count" in metric_result:
        return int(metric_result.get("recurrent_item_count") or 0)
    return int(metric_result.get("invalid_count") or 0)


def _generation_error_record(error: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "prompt_id": error.get("prompt_id"),
        "model": error.get("model"),
        "artifact_path": None,
        "generation_status": "error",
        "evaluation_status": "skipped",
        "generation_error": {
            "error_type": error.get("error_type"),
            "error_message": error.get("error_message"),
            "stack_trace": error.get("stack_trace"),
        },
        "contract": None,
        "metrics": {},
        "overall_hallucination_failed": False,
        "overall_failure_categories": [],
    }


def _evaluation_error_record(
    *,
    prompt_id: str,
    model: str,
    artifact_path: str | None,
    error: Exception,
) -> Dict[str, Any]:
    return {
        "prompt_id": prompt_id,
        "model": model,
        "artifact_path": artifact_path,
        "generation_status": "ok",
        "evaluation_status": "error",
        "evaluation_error": {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "stack_trace": traceback.format_exc(),
        },
        "contract": None,
        "metrics": {},
        "overall_hallucination_failed": False,
        "overall_failure_categories": [],
    }


def _write_metric_csv(path: Path, metric_summary: Mapping[str, Mapping[str, Any]]) -> None:
    rows = list(metric_summary.values())
    _write_rows(path, rows, fieldnames=[
        "metric",
        "samples_evaluated",
        "samples_failed",
        "sample_failure_rate",
        "total_observations",
        "invalid_observations",
        "observation_error_rate",
    ])


def _write_group_csv(path: Path, group_summary: Mapping[str, Mapping[str, Any]]) -> None:
    rows = [{"group": group, **stats} for group, stats in group_summary.items()]
    _write_rows(path, rows, fieldnames=["group", "samples_evaluated", "samples_failed", "sample_failure_rate"])


def _write_top_hallucinations_csv(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    counter: Counter[tuple[str, str]] = Counter()
    for record in records:
        for item in invalid_items_for_recurrence(record):
            counter[(item["category"], item["item"])] += 1
    rows = [
        {"category": category, "item": item, "count": count}
        for (category, item), count in counter.most_common()
    ]
    _write_rows(path, rows, fieldnames=["category", "item", "count"])


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]], *, fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _load_prompt_map(path: Path) -> Dict[str, str]:
    prompt_map: Dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("prompt_id") and record.get("prompt"):
                prompt_map[str(record["prompt_id"])] = str(record["prompt"])
    return prompt_map


def _infer_prompt_and_model(path: Path) -> tuple[str, str]:
    stem = path.stem
    for alias in sorted(ALL_MODEL_ALIASES, key=len, reverse=True):
        suffix = f"_{alias}"
        if stem.endswith(suffix):
            return stem[: -len(suffix)], alias
    if "_" in stem:
        prompt_id, model = stem.rsplit("_", 1)
        return prompt_id, model
    return stem, "unknown"
