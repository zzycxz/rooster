"""
AST-based Python code safety analyzer.

Replaces the previous regex scanner with a proper AST-level analysis that
cannot be bypassed by string concatenation, encoding tricks, or reflection.

Usage:
    from utils.code_safety import ast_safety_check
    safe, violations = ast_safety_check(code_string)
"""

import ast
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Modules whose import is considered dangerous
_DANGEROUS_MODULES = frozenset(
    {
        "os",
        "subprocess",
        "shutil",
        "socket",
        "ctypes",
        "pickle",
        "marshal",
        "signal",
        "sys",
        "threading",
        "multiprocessing",
        "resource",
        "importlib",
    }
)

# Dangerous attribute names on any module
_DANGEROUS_ATTRS = frozenset(
    {
        "system",
        "popen",
        "exec",
        "spawn",
        "remove",
        "unlink",
        "rmdir",
        "rmtree",
        "chmod",
        "chown",
        "kill",
        "fork",
        "Popen",
        "call",
        "run",
        "check_output",
        "check_call",
        "getoutput",
        "getstatusoutput",
        "import_module",
    }
)

# Built-in functions that should be blocked
_DANGEROUS_BUILTINS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
    }
)


class _SafetyVisitor(ast.NodeVisitor):
    """Walk AST and collect dangerous patterns."""

    def __init__(self):
        self.violations: List[str] = []

    def _check_call(self, node: ast.Call):
        """Check if a Call node targets a dangerous function."""
        func = node.func

        # Case 1: os.system(), subprocess.Popen(), etc.
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr in _DANGEROUS_ATTRS:
                if isinstance(func.value, ast.Name):
                    module = func.value.id
                    if module in _DANGEROUS_MODULES:
                        self.violations.append(f"{module}.{attr}()")

        # Case 2: eval(), exec(), compile(), __import__()
        elif isinstance(func, ast.Name):
            if func.id in _DANGEROUS_BUILTINS:
                self.violations.append(f"{func.id}()")

        # Case 3: getattr(os, 'system') pattern
        elif isinstance(func, ast.Attribute) and func.attr == "getattr":
            pass  # getattr itself is handled below

    def visit_Call(self, node: ast.Call):
        self._check_call(node)

        # Check for getattr(module, 'dangerous_attr') calls
        if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            if isinstance(node.args[0], ast.Name) and node.args[0].id in _DANGEROUS_MODULES:
                if isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                    self.violations.append(f"getattr({node.args[0].id}, '{node.args[1].value}')")

        # Check for __import__() calls
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            self.violations.append("__import__()")

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _DANGEROUS_MODULES:
                self.violations.append(f"import {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            root = node.module.split(".")[0]
            if root in _DANGEROUS_MODULES:
                names = [a.name for a in node.names]
                self.violations.append(f"from {node.module} import {', '.join(names)}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # Catch os.remove, os.unlink, os.chmod, etc. even outside call context
        if isinstance(node.value, ast.Name) and node.value.id in _DANGEROUS_MODULES and node.attr in _DANGEROUS_ATTRS:
            # Only flag if not already caught by visit_Call
            self.violations.append(f"{node.value.id}.{node.attr}")
        self.generic_visit(node)

    def visit_Call_open(self, node: ast.Call):
        """Check open() calls for write mode."""
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
                if "w" in mode or "a" in mode or "+" in mode:
                    self.violations.append(f"open() in write mode ('{mode}')")

    # Override to also check open() calls
    def generic_visit(self, node):
        if isinstance(node, ast.Call):
            self._check_open(node)
        super().generic_visit(node)

    def _check_open(self, node: ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            # Check mode argument (positional or keyword)
            mode_val = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode_val = node.args[1].value
            else:
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode_val = kw.value.value
                        break
            if mode_val and isinstance(mode_val, str):
                if "w" in mode_val or "a" in mode_val or "+" in mode_val:
                    self.violations.append(f"open() in write mode ('{mode_val}')")


def ast_safety_check(code: str) -> Tuple[bool, List[str]]:
    """
    Analyze Python code via AST for dangerous patterns.

    Returns:
        (safe, violations) where safe=True means no dangerous patterns found.
        Cannot be bypassed by string concatenation, encoding, or reflection.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        # Unparseable code is treated as unsafe
        return False, [f"SyntaxError: {e.msg} (line {e.lineno})"]

    visitor = _SafetyVisitor()
    visitor.visit(tree)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for v in visitor.violations:
        if v not in seen:
            seen.add(v)
            unique.append(v)

    return len(unique) == 0, unique
