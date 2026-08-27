import pytest

from app.services.finetuning_service import (
    BASE_MODEL_SPECS,
    GPU_HARDWARE_SPECS,
    calculate_trainable_parameters,
    estimate_finetuning_vram,
    estimate_training_duration_and_cost,
    generate_unsloth_recipe,
)


def test_base_models_and_hardware_specs():
    assert "llama-3.1-8b" in BASE_MODEL_SPECS
    assert "llama-3.3-70b" in BASE_MODEL_SPECS
    assert "deepseek-r1-qwen-32b" in BASE_MODEL_SPECS

    assert "tesla_v100_32" in GPU_HARDWARE_SPECS
    assert "tesla_p40_24" in GPU_HARDWARE_SPECS
    assert GPU_HARDWARE_SPECS["tesla_v100_32"]["vram_gb"] == 32


def test_trainable_parameters_calculation():
    res = calculate_trainable_parameters("llama-3.1-8b", lora_r=16)
    assert res["total_parameters"] > 8_000_000_000
    assert res["trainable_parameters"] > 0
    assert res["trainable_percent"] < 1.0  # LoRA is typically < 1% of base weights
    assert res["adapter_size_mb"] > 0


def test_vram_estimation_qlora_vs_full():
    qlora_res = estimate_finetuning_vram(
        model_key="llama-3.1-8b",
        method="qlora_4bit",
        lora_r=16,
        context_length=4096,
    )
    assert qlora_res["breakdown_gb"]["total_vram"] < 20.0
    assert qlora_res["multi_gpu_needed"] is False
    assert len(qlora_res["recommended_hardware"]) > 0

    full_res = estimate_finetuning_vram(
        model_key="llama-3.1-8b",
        method="full_16bit",
        context_length=4096,
    )
    # Full 16-bit requires significantly more VRAM than 4-bit QLoRA
    assert full_res["breakdown_gb"]["total_vram"] > qlora_res["breakdown_gb"]["total_vram"]


def test_vram_estimation_70b_multi_gpu():
    res = estimate_finetuning_vram(
        model_key="llama-3.3-70b",
        method="lora_16bit",
        lora_r=32,
        context_length=8192,
    )
    assert res["breakdown_gb"]["total_vram"] > 140.0
    assert res["multi_gpu_needed"] is True
    assert res["gpus_required"] >= 4


def test_training_duration_and_cost():
    res = estimate_training_duration_and_cost(
        model_key="llama-3.1-8b",
        dataset_tokens=2_000_000,
        epochs=3,
        hardware_key="tesla_v100_32",
        method="qlora_4bit",
    )
    assert res["training_hours"] > 0
    assert res["cost_usd"] > 0
    assert res["throughput_tokens_sec"] > 0
    assert res["savings_usd"] >= 0


def test_generate_unsloth_recipe():
    script = generate_unsloth_recipe(
        model_key="llama-3.1-8b",
        lora_r=16,
        lora_alpha=32,
        context_length=4096,
        learning_rate=2e-4,
        epochs=3,
    )
    assert "FastLanguageModel" in script
    assert "load_in_4bit=True" in script
    assert "r=16" in script
    assert "num_train_epochs=3" in script
    assert "save_pretrained" in script
