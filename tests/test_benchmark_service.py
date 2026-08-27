import pytest

from app.services.benchmark_service import (
    calculate_kv_cache_scaling,
    get_model_benchmark_detail,
    list_benchmark_models,
)


def test_list_benchmark_models():
    models = list_benchmark_models()
    assert len(models) >= 5
    keys = [m["key"] for m in models]
    assert "llama-3.3-70b" in keys
    assert "llama-3.1-8b" in keys
    assert "deepseek-r1-qwen-32b" in keys


def test_get_model_benchmark_detail():
    detail = get_model_benchmark_detail("llama-3.3-70b")
    assert detail is not None
    assert detail["parameters_b"] > 70
    assert "FP16" in detail["quants"]
    assert "Q4_K_M" in detail["quants"]
    assert "MMLU" in detail["benchmarks"]
    assert detail["typical_latency_p50_ms"] > 0


def test_calculate_kv_cache_scaling():
    scaling_fp16 = calculate_kv_cache_scaling(
        model_key="llama-3.1-8b",
        context_tokens=8192,
        num_users=4,
        quant_cache="fp16",
    )
    assert scaling_fp16["kv_cache_mb"] > 0
    assert scaling_fp16["kv_cache_gb"] > 0

    scaling_fp8 = calculate_kv_cache_scaling(
        model_key="llama-3.1-8b",
        context_tokens=8192,
        num_users=4,
        quant_cache="fp8",
    )
    # FP8 KV cache is approximately half the size of FP16
    assert scaling_fp8["kv_cache_mb"] < scaling_fp16["kv_cache_mb"]
