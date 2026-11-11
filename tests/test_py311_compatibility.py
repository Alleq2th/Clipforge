import ast
import glob
import os
import unittest

class TestPython311Compatibility(unittest.TestCase):
    def test_no_nested_fstring_quotes_or_backslashes(self):
        """
        Render runs Python 3.11 (PEP 701 arbitrary quote nesting in f-strings was added in 3.12).
        Ensure no Python files use nested quotes matching the f-string delimiter or backslashes
        inside f-string expressions, which causes Gunicorn / Python 3.11 boot failure.
        """
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        issues = []
        
        for py_file in glob.glob(os.path.join(root_dir, "**", "*.py"), recursive=True):
            if "venv" in py_file or ".git" in py_file:
                continue
            with open(py_file, "r", encoding="utf-8") as f:
                src = f.read()
            
            tree = ast.parse(src, py_file)
            lines = src.splitlines()
            
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    start_line = node.lineno
                    f_slice = lines[start_line - 1][node.col_offset:]
                    for delim in ('"""', "'''", '"', "'"):
                        if f_slice.startswith("f" + delim) or f_slice.startswith("F" + delim):
                            for val in node.values:
                                if isinstance(val, ast.FormattedValue):
                                    val_expr = ast.get_source_segment(src, val.value) or ""
                                    if delim in val_expr:
                                        rel_path = os.path.relpath(py_file, root_dir)
                                        issues.append(f"{rel_path}:{val.lineno} - delimiter {delim} inside expression: {val_expr}")
                                    if "\\\\" in val_expr:
                                        rel_path = os.path.relpath(py_file, root_dir)
                                        issues.append(f"{rel_path}:{val.lineno} - backslash inside expression: {val_expr}")
                            break
                            
        self.assertEqual(issues, [], f"Found Python 3.11 incompatible f-strings: {issues}")

if __name__ == "__main__":
    unittest.main()
