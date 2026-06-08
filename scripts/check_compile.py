#!/usr/bin/env python3
"""Quick compilation check for new modules."""
import py_compile
import sys

files = [
    "bot/agents/odata/state.py",
    "bot/agents/odata/tool_resolver.py",
    "bot/agents/odata/ai_service.py",
    "bot/agents/odata/query_executor.py",
    "bot/agents/odata/query_validator.py",
    "bot/agents/odata/error_handler.py",
    "bot/agents/odata/pipeline.py",
]

ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  OK: {f}")
    except py_compile.PyCompileError as e:
        print(f"  FAIL: {f} -> {e}")
        ok = False

if ok:
    print("\nAll 7 modules compile OK")
else:
    print("\nSome modules failed!")
    sys.exit(1)
