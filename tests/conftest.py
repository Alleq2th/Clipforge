import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The picker imports the AI helper at module load; tests replace ask_json, so a stub is enough.
if "llm" not in sys.modules:
    stub = types.ModuleType("llm")
    stub.ask_json = lambda prompt: {"clips": []}
    sys.modules["llm"] = stub
