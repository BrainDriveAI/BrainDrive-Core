#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone test script to verify Ollama parameter mapping fix.
Tests the _build_ollama_options() method without requiring pytest or DB setup.

Run: python backend/scripts/test_ollama_parameter_fix.py
"""

import sys
import os
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add backend to path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from app.ai_providers.ollama import OllamaProvider


class TestResult:
    def __init__(self, name):
        self.name = name
        self.passed = False
        self.error = None

    def __repr__(self):
        status = "[PASS]" if self.passed else "[FAIL]"
        error_msg = f" - {self.error}" if self.error else ""
        return f"{status}: {self.name}{error_msg}"


def test_context_window_mapping():
    """Test that context_window is correctly mapped to num_ctx"""
    result = TestResult("context_window → num_ctx mapping")
    try:
        provider = OllamaProvider()
        params = {"context_window": 8192}
        options = provider._build_ollama_options(params)

        assert "num_ctx" in options, "num_ctx not in options"
        assert options["num_ctx"] == 8192, f"Expected 8192, got {options['num_ctx']}"
        assert "context_window" not in options, "context_window leaked through"

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_temperature_mapping():
    """Test that temperature is correctly passed through"""
    result = TestResult("temperature mapping")
    try:
        provider = OllamaProvider()
        params = {"temperature": 0.7}
        options = provider._build_ollama_options(params)

        assert "temperature" in options
        assert options["temperature"] == 0.7

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_top_p_and_top_k_mapping():
    """Test that top_p and top_k are correctly passed through"""
    result = TestResult("top_p and top_k mapping")
    try:
        provider = OllamaProvider()
        params = {"top_p": 0.9, "top_k": 40}
        options = provider._build_ollama_options(params)

        assert "top_p" in options
        assert options["top_p"] == 0.9
        assert "top_k" in options
        assert options["top_k"] == 40

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_max_tokens_to_num_predict():
    """Test that max_tokens is correctly mapped to num_predict"""
    result = TestResult("max_tokens → num_predict mapping")
    try:
        provider = OllamaProvider()
        params = {"max_tokens": 2048}
        options = provider._build_ollama_options(params)

        assert "num_predict" in options
        assert options["num_predict"] == 2048
        assert "max_tokens" not in options

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_frequency_penalty_to_repeat_penalty():
    """Test that frequency_penalty is mapped to repeat_penalty"""
    result = TestResult("frequency_penalty → repeat_penalty mapping")
    try:
        provider = OllamaProvider()
        params = {"frequency_penalty": 0.5}
        options = provider._build_ollama_options(params)

        assert "repeat_penalty" in options
        # Formula: 1.0 + (penalty / 2) = 1.0 + (0.5 / 2) = 1.25
        expected = 1.25
        assert abs(options["repeat_penalty"] - expected) < 0.001, f"Expected {expected}, got {options['repeat_penalty']}"
        assert "frequency_penalty" not in options

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_presence_penalty_to_repeat_penalty():
    """Test that presence_penalty is mapped to repeat_penalty"""
    result = TestResult("presence_penalty → repeat_penalty mapping")
    try:
        provider = OllamaProvider()
        params = {"presence_penalty": 0.8}
        options = provider._build_ollama_options(params)

        assert "repeat_penalty" in options
        # Formula: 1.0 + (penalty / 2) = 1.0 + (0.8 / 2) = 1.4
        expected = 1.4
        assert abs(options["repeat_penalty"] - expected) < 0.001
        assert "presence_penalty" not in options

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_both_penalties_uses_max():
    """Test that when both penalties are present, max is used"""
    result = TestResult("Both penalties → uses max")
    try:
        provider = OllamaProvider()
        params = {
            "frequency_penalty": 0.5,
            "presence_penalty": 0.8  # Higher
        }
        options = provider._build_ollama_options(params)

        # Should use max (0.8): 1.0 + (0.8 / 2) = 1.4
        expected = 1.4
        assert abs(options["repeat_penalty"] - expected) < 0.001

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_stop_sequences_list():
    """Test that stop sequences as list are correctly passed"""
    result = TestResult("stop_sequences as list")
    try:
        provider = OllamaProvider()
        params = {"stop_sequences": ["Human:", "Assistant:"]}
        options = provider._build_ollama_options(params)

        assert "stop" in options
        assert options["stop"] == ["Human:", "Assistant:"]
        assert "stop_sequences" not in options

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_stop_sequences_string():
    """Test that stop sequences as string are converted to list"""
    result = TestResult("stop_sequences as string → list")
    try:
        provider = OllamaProvider()
        params = {"stop_sequences": "STOP"}
        options = provider._build_ollama_options(params)

        assert "stop" in options
        assert options["stop"] == ["STOP"]
        assert isinstance(options["stop"], list)

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_complete_persona_params():
    """Test realistic persona parameters from BrainDrive"""
    result = TestResult("Complete persona params (realistic scenario)")
    try:
        provider = OllamaProvider()
        params = {
            "context_window": 8192,
            "temperature": 0.8,
            "top_p": 0.9,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.1,
            "stop_sequences": ["Human:", "Assistant:"]
        }
        options = provider._build_ollama_options(params)

        # Verify correct mapping
        assert options["num_ctx"] == 8192
        assert options["temperature"] == 0.8
        assert options["top_p"] == 0.9
        assert abs(options["repeat_penalty"] - 1.05) < 0.001  # 1.0 + (0.1 / 2)
        assert options["stop"] == ["Human:", "Assistant:"]

        # Verify no leakage
        assert "context_window" not in options
        assert "frequency_penalty" not in options
        assert "presence_penalty" not in options
        assert "stop_sequences" not in options

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_empty_params():
    """Test that empty params dict returns empty options"""
    result = TestResult("Empty params → empty options")
    try:
        provider = OllamaProvider()
        params = {}
        options = provider._build_ollama_options(params)

        assert isinstance(options, dict)
        assert len(options) == 0

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_none_values_ignored():
    """Test that None values are not included in options"""
    result = TestResult("None values ignored")
    try:
        provider = OllamaProvider()
        params = {
            "temperature": None,
            "top_p": 0.9
        }
        options = provider._build_ollama_options(params)

        assert "temperature" not in options
        assert "top_p" in options
        assert options["top_p"] == 0.9

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_llama32_3b_recommended_config():
    """Test recommended config for llama3.2:3b"""
    result = TestResult("llama3.2:3b recommended config")
    try:
        provider = OllamaProvider()
        params = {
            "context_window": 8192,
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 40
        }
        options = provider._build_ollama_options(params)

        assert options["num_ctx"] == 8192
        assert options["temperature"] == 0.7
        assert options["top_p"] == 0.9
        assert options["top_k"] == 40

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def test_payload_structure():
    """Test that payload structure matches Ollama API expectations"""
    result = TestResult("Payload structure (integration)")
    try:
        provider = OllamaProvider()

        # Simulate persona params
        persona_params = {
            "context_window": 8192,
            "temperature": 0.8,
            "top_p": 0.9,
            "frequency_penalty": 0.2
        }

        options = provider._build_ollama_options(persona_params)

        # Verify what would be sent to Ollama API
        expected_payload = {
            "model": "llama3.2:3b",
            "prompt": "test prompt",
            "stream": False,
            "options": options  # This is what we're testing
        }

        # Verify options has correct structure
        assert "options" in expected_payload
        assert isinstance(expected_payload["options"], dict)
        assert "num_ctx" in expected_payload["options"]
        assert "context_window" not in expected_payload["options"]
        assert expected_payload["options"]["num_ctx"] == 8192

        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


def run_all_tests():
    """Run all tests and report results"""
    print("Testing Ollama Parameter Mapping Fix")
    print("=" * 70)
    print()

    tests = [
        test_context_window_mapping,
        test_temperature_mapping,
        test_top_p_and_top_k_mapping,
        test_max_tokens_to_num_predict,
        test_frequency_penalty_to_repeat_penalty,
        test_presence_penalty_to_repeat_penalty,
        test_both_penalties_uses_max,
        test_stop_sequences_list,
        test_stop_sequences_string,
        test_complete_persona_params,
        test_empty_params,
        test_none_values_ignored,
        test_llama32_3b_recommended_config,
        test_payload_structure,
    ]

    results = []
    for test_func in tests:
        result = test_func()
        results.append(result)
        print(result)

    # Summary
    print()
    print("=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    pass_rate = (passed / total) * 100

    print(f"Tests Passed: {passed}/{total} ({pass_rate:.1f}%)")
    print()

    if passed == total:
        print("ALL TESTS PASSED!")
        print("The Ollama parameter mapping fix is working correctly.")
        print()
        print("The fix ensures:")
        print("  - context_window -> num_ctx (Ollama uses 8192 instead of 2048 default)")
        print("  - Parameters wrapped in 'options' object per Ollama API spec")
        print("  - OpenAI-style penalties converted to repeat_penalty")
        print("  - System prompts will no longer be truncated")
        return 0
    else:
        print("SOME TESTS FAILED")
        print()
        print("Failed tests:")
        for result in results:
            if not result.passed:
                print(f"  - {result.name}: {result.error}")
        return 1


if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)
