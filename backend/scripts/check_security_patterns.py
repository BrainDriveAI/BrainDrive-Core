#!/usr/bin/env python3
"""
Security pattern validation guard for BrainDrive.
Run this before commits or in CI to prevent security regressions.

Exit codes:
  0 - All checks passed
  1 - Security violations found
"""
import subprocess
import sys
from pathlib import Path


def check_oauth2_scheme_usage():
    """
    Ensure oauth2_scheme is only used in the auth dependency module.
    
    
    "Add a simple CI/test guard: fail if oauth2_scheme is used outside 
    the auth dependency module."
    """
    print("🔍 Checking oauth2_scheme usage...")
    print("-" * 80)
    
    # Allowed files
    allowed_files = {
        "backend/app/core/security.py",
        "backend/app/core/auth_deps.py"
    }
    
    backend_app_dir = Path(__file__).parent.parent / "app"
    if not backend_app_dir.exists():
        print(f"  ⚠️  WARNING: {backend_app_dir} not found")
        return True
    
    violations = []
    
    # Walk through all .py files in backend/app
    for py_file in backend_app_dir.rglob("*.py"):
        # Skip __pycache__ and similar
        if "__pycache__" in str(py_file):
            continue
        
        # Check if this is an allowed file
        rel_path = str(py_file.relative_to(backend_app_dir.parent.parent))
        is_allowed = any(rel_path.endswith(allowed.replace("backend/", "")) for allowed in allowed_files)
        
        # Search for oauth2_scheme in file
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    if 'oauth2_scheme' in line:
                        if not is_allowed:
                            violations.append((rel_path, line_num, line.strip()))
        except Exception as e:
            print(f"  ⚠️  Error reading {py_file}: {e}")
    
    if violations:
        print("  ❌ VIOLATION: oauth2_scheme used outside auth modules!")
        print("\n  Found in:")
        for filepath, line_num, line_content in violations:
            print(f"    {filepath}:{line_num}")
            print(f"      {line_content[:100]}")
        print("\n  ⚠️  SECURITY RULE:")
        print("     oauth2_scheme should ONLY be used in:")
        print("       - backend/app/core/security.py")
        print("       - backend/app/core/auth_deps.py")
        print("\n  💡 FIX: Use require_user or require_admin instead")
        return False
    else:
        print("  ✅ oauth2_scheme only in auth modules (allowed)")
        return True


def main():
    """Run all security checks."""
    print("=" * 80)
    print("BrainDrive Security Pattern Validation")
    print("Phase Checked")
    print("=" * 80)
    print()
    
    checks = [
        ("oauth2_scheme Usage Check", check_oauth2_scheme_usage),
    ]
    
    results = []
    for check_name, check_func in checks:
        print(f"\n{check_name}")
        passed = check_func()
        results.append((check_name, passed))
        print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    all_passed = all(passed for _, passed in results)
    
    for check_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {check_name}")
    
    print()
    if all_passed:
        print("✅ All security checks passed!")
        print("=" * 80)
        return 0
    else:
        print("❌ Security violations found - see details above")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())

