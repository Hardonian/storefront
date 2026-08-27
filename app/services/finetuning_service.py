"""Fine-Tuning & LoRA / QLoRA parameterization, VRAM budgeting, and script generation engine."""

from __future__ import annotations

import math
from typing import Any

# Standard GPU profiles in the sovereign stack
GPU_HARDWARE_SPECS: dict[str, dict[str, Any]] = {
    "tesla_v100_32": {
        "name": "NVIDIA Tesla V100 SXM2 (32GB VRAM)",
        "vram_gb": 32,
        "tflops_fp16": 125,
        "mem_bandwidth_gb_s": 900,
        "hourly_cents": 2000,
        "typical_power_w": 300,
        "recommended_for": "QLoRA 70B / LoRA 8B-33B / Batch Inference",
    },
    "tesla_p40_24": {
        "name": "NVIDIA Tesla P40 (24GB VRAM)",
        "vram_gb": 24,
        "tflops_fp16": 47,  # Pascal FP32 optimized
        "mem_bandwidth_gb_s": 346,
        "hourly_cents": 1200,
        "typical_power_w": 250,
        "recommended_for": "QLoRA 8B-14B / GGUF CPU+GPU Offload / Cost-First",
    },
    "rtx_4090_24": {
        "name": "NVIDIA GeForce RTX 4090 (24GB VRAM)",
        "vram_gb": 24,
        "tflops_fp16": 165,
        "mem_bandwidth_gb_s": 1008,
        "hourly_cents": 1800,
        "typical_power_w": 450,
        "recommended_for": "Ultra-Fast LoRA 8B / Diffusion LoRA Training",
    },
    "a100_80": {
        "name": "NVIDIA A100 SXM4 (80GB VRAM)",
        "vram_gb": 80,
        "tflops_fp16": 312,
        "mem_bandwidth_gb_s": 2039,
        "hourly_cents": 4500,
        "typical_power_w": 400,
        "recommended_for": "Full LoRA 70B / Dense Multi-Epoch Training",
    },
    "h100_80": {
        "name": "NVIDIA H100 SXM5 (80GB VRAM)",
        "vram_gb": 80,
        "tflops_fp16": 989,  # FP8/FP16 Transformer Engine
        "mem_bandwidth_gb_s": 3350,
        "hourly_cents": 8500,
        "typical_power_w": 700,
        "recommended_for": "Enterprise Pre-training & Large-Scale Instruction Tuning",
    },
}

# Base model architectures and default target modules
BASE_MODEL_SPECS: dict[str, dict[str, Any]] = {
    "llama-3.1-8b": {
        "name": "Meta Llama 3.1 8B Instruct",
        "params_b": 8.03,
        "layers": 32,
        "hidden_size": 4096,
        "vocab_size": 128256,
        "default_target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        "recommended_lr": 2e-4,
    },
    "llama-3.3-70b": {
        "name": "Meta Llama 3.3 70B Instruct",
        "params_b": 70.6,
        "layers": 80,
        "hidden_size": 8192,
        "vocab_size": 128256,
        "default_target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        "recommended_lr": 1e-4,
    },
    "deepseek-r1-qwen-32b": {
        "name": "DeepSeek R1 Distill Qwen 32B",
        "params_b": 32.8,
        "layers": 64,
        "hidden_size": 5120,
        "vocab_size": 152064,
        "default_target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        "recommended_lr": 1.5e-4,
    },
    "mistral-nemo-12b": {
        "name": "Mistral NeMo 12B Base/Instruct",
        "params_b": 12.2,
        "layers": 40,
        "hidden_size": 5120,
        "vocab_size": 131072,
        "default_target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        "recommended_lr": 2e-4,
    },
    "qwen-2.5-coder-32b": {
        "name": "Qwen 2.5 Coder 32B Instruct",
        "params_b": 32.5,
        "layers": 64,
        "hidden_size": 5120,
        "vocab_size": 152064,
        "default_target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        "recommended_lr": 1.5e-4,
    },
    "phi-3.5-mini-3.8b": {
        "name": "Microsoft Phi 3.5 Mini (3.8B)",
        "params_b": 3.82,
        "layers": 32,
        "hidden_size": 3072,
        "vocab_size": 32064,
        "default_target_modules": ["o_proj", "qkv_proj", "gate_up_proj", "down_proj"],
        "recommended_lr": 3e-4,
    },
}


def calculate_trainable_parameters(
    model_key: str,
    lora_r: int = 16,
    num_target_modules: int = 7,
) -> dict[str, Any]:
    """Calculate the number of trainable parameters for LoRA adapter vs total base model weights."""
    model_spec = BASE_MODEL_SPECS.get(model_key, BASE_MODEL_SPECS["llama-3.1-8b"])
    hidden_size = model_spec["hidden_size"]
    layers = model_spec["layers"]
    total_params = int(model_spec["params_b"] * 1e9)

    # LoRA parameters per linear module = 2 * hidden_size * r
    params_per_module = 2 * hidden_size * lora_r
    total_lora_params = params_per_module * num_target_modules * layers

    pct_trainable = (total_lora_params / total_params) * 100.0

    return {
        "total_parameters": total_params,
        "trainable_parameters": total_lora_params,
        "trainable_percent": round(pct_trainable, 4),
        "adapter_size_mb": round((total_lora_params * 2) / (1024 * 1024), 2),  # FP16 weights
    }


def estimate_finetuning_vram(
    model_key: str,
    method: str = "qlora_4bit",  # "qlora_4bit", "lora_8bit", "lora_16bit", "full_16bit"
    lora_r: int = 16,
    context_length: int = 4096,
    batch_size: int = 2,
    gradient_accumulation_steps: int = 4,
    use_gradient_checkpointing: bool = True,
    use_flash_attention_2: bool = True,
    optimizer: str = "adamw_8bit",  # "adamw_8bit", "adamw_32bit", "paged_adamw_8bit"
) -> dict[str, Any]:
    """Calculate exact fine-tuning VRAM memory breakdown and GPU topology recommendation."""
    model_spec = BASE_MODEL_SPECS.get(model_key, BASE_MODEL_SPECS["llama-3.1-8b"])
    params_b = model_spec["params_b"]
    layers = model_spec["layers"]
    hidden_size = model_spec["hidden_size"]

    # 1. Base Model Weights VRAM (GB)
    if method == "qlora_4bit":
        base_weights_gb = params_b * 0.55  # 4-bit weights + quant scales
    elif method == "lora_8bit":
        base_weights_gb = params_b * 1.05  # 8-bit weights
    elif method in ("lora_16bit", "full_16bit"):
        base_weights_gb = params_b * 2.05  # 16-bit FP16/BF16
    else:
        base_weights_gb = params_b * 0.55

    # 2. Trainable Parameters & Optimizer State VRAM (GB)
    trainable_info = calculate_trainable_parameters(model_key, lora_r=lora_r)
    trainable_params_count = trainable_info["trainable_parameters"] if method != "full_16bit" else int(params_b * 1e9)

    # Optimizer state: AdamW needs 2 states (momentum + variance)
    # 8-bit optimizer = 2 bytes per param; 32-bit optimizer = 8 bytes per param
    opt_bytes_per_param = 2.5 if "8bit" in optimizer else 8.5
    # Gradients = 2 bytes per trainable param (FP16/BF16)
    grad_bytes_per_param = 2.0
    # Master weights (if mixed precision full) = 4 bytes per param
    master_weights_bytes = 4.0 if method == "full_16bit" else 2.0

    trainable_state_bytes = trainable_params_count * (opt_bytes_per_param + grad_bytes_per_param + master_weights_bytes)
    trainable_state_gb = trainable_state_bytes / (1024**3)

    # 3. Activation Memory VRAM (GB)
    # Per layer activation memory with FlashAttention & selective checkpointing
    # Standard: ~ b * s * h * (10 + 24/k) bytes. With gradient checkpointing: ~ b * s * h * 2.5 bytes
    token_batch = batch_size * context_length
    if use_gradient_checkpointing:
        act_multiplier = 2.2 if use_flash_attention_2 else 4.0
        activation_gb = (token_batch * hidden_size * act_multiplier * math.sqrt(layers)) / (1024**3)
    else:
        act_multiplier = 12.0 if use_flash_attention_2 else 24.0
        activation_gb = (token_batch * hidden_size * layers * act_multiplier) / (1024**3)

    # 4. CUDA Workspace & PyTorch allocator overhead (GB)
    overhead_gb = max(1.2, (base_weights_gb + trainable_state_gb + activation_gb) * 0.12)

    total_vram_gb = round(base_weights_gb + trainable_state_gb + activation_gb + overhead_gb, 2)

    # Hardware topology recommendation
    recommended_hardware = []
    for hw_key, hw in GPU_HARDWARE_SPECS.items():
        if hw["vram_gb"] >= total_vram_gb:
            headroom_gb = round(hw["vram_gb"] - total_vram_gb, 1)
            recommended_hardware.append({
                "key": hw_key,
                "name": hw["name"],
                "vram_gb": hw["vram_gb"],
                "headroom_gb": headroom_gb,
                "fits": True,
                "hourly_cost_usd": round(hw["hourly_cents"] / 100.0, 2),
            })

    # If single GPU cannot fit, calculate multi-GPU tensor / pipeline parallel
    multi_gpu_needed = False
    gpus_required = 1
    if not recommended_hardware:
        multi_gpu_needed = True
        # Calculate minimum V100 (32GB) or A100 (80GB) cards
        gpus_required = math.ceil(total_vram_gb / 28.0)  # 28GB usable per 32GB card
        recommended_hardware.append({
            "key": f"{gpus_required}x_v100_32",
            "name": f"{gpus_required}x NVIDIA Tesla V100 32GB ({gpus_required * 32}GB VRAM Cluster)",
            "vram_gb": gpus_required * 32,
            "headroom_gb": round((gpus_required * 32) - total_vram_gb, 1),
            "fits": True,
            "hourly_cost_usd": round((gpus_required * 2000) / 100.0, 2),
        })

    return {
        "model_key": model_key,
        "model_name": model_spec["name"],
        "method": method,
        "lora_r": lora_r,
        "context_length": context_length,
        "batch_size": batch_size,
        "effective_batch_size": batch_size * gradient_accumulation_steps,
        "breakdown_gb": {
            "base_weights": round(base_weights_gb, 2),
            "trainable_and_optimizer": round(trainable_state_gb, 2),
            "activations": round(activation_gb, 2),
            "cuda_overhead": round(overhead_gb, 2),
            "total_vram": total_vram_gb,
        },
        "trainable_summary": trainable_info,
        "multi_gpu_needed": multi_gpu_needed,
        "gpus_required": gpus_required,
        "recommended_hardware": recommended_hardware,
    }


def estimate_training_duration_and_cost(
    model_key: str,
    dataset_tokens: int = 5_000_000,  # 5 million tokens (~10,000 instructions)
    epochs: int = 3,
    hardware_key: str = "tesla_v100_32",
    method: str = "qlora_4bit",
) -> dict[str, Any]:
    """Calculate expected training hours, token throughput (tokens/sec), and cost."""
    model_spec = BASE_MODEL_SPECS.get(model_key, BASE_MODEL_SPECS["llama-3.1-8b"])
    hw = GPU_HARDWARE_SPECS.get(hardware_key, GPU_HARDWARE_SPECS["tesla_v100_32"])

    params_b = model_spec["params_b"]
    # Total FLOPs for forward + backward pass: ~ 6 FLOPs per parameter per token (LoRA has lower backward pass ~ 3.5 FLOPs)
    flops_per_token = (3.8 if "lora" in method else 6.0) * params_b * 1e9
    total_tokens_trained = dataset_tokens * epochs
    total_flops = flops_per_token * total_tokens_trained

    # Hardware compute efficiency (MFU: Model FLOPs Utilization ~ 35-45% on modern kernels)
    mfu = 0.38
    effective_tflops = hw["tflops_fp16"] * mfu
    flops_per_second = effective_tflops * 1e12

    training_seconds = max(60, total_flops / max(1.0, flops_per_second))
    training_hours = round(training_seconds / 3600.0, 2)
    tokens_per_sec = round(total_tokens_trained / max(1.0, training_seconds), 1)

    cost_usd = round(training_hours * (hw["hourly_cents"] / 100.0), 2)
    cloud_competitor_cost_usd = round(training_hours * 3.85, 2)  # Avg hyperscaler on-demand rate
    savings_usd = max(0.0, round(cloud_competitor_cost_usd - cost_usd, 2))

    return {
        "dataset_tokens": dataset_tokens,
        "epochs": epochs,
        "total_tokens_processed": total_tokens_trained,
        "hardware": hw["name"],
        "throughput_tokens_sec": tokens_per_sec,
        "training_hours": training_hours,
        "cost_usd": cost_usd,
        "cloud_competitor_cost_usd": cloud_competitor_cost_usd,
        "savings_usd": savings_usd,
        "efficiency_mfu_pct": round(mfu * 100, 1),
    }


def generate_unsloth_recipe(
    model_key: str,
    lora_r: int = 16,
    lora_alpha: int = 32,
    context_length: int = 4096,
    learning_rate: float = 2e-4,
    epochs: int = 3,
    output_dir: str = "./sovereign-lora-adapter",
) -> str:
    """Generate a high-performance local Unsloth / Hugging Face training script."""
    model_spec = BASE_MODEL_SPECS.get(model_key, BASE_MODEL_SPECS["llama-3.1-8b"])
    model_name = model_spec["name"]
    targets = model_spec["default_target_modules"]
    targets_str = str(targets)

    return f'''# Sovereign Fine-Tuning Recipe for {model_name}
# Engine: Unsloth + Hugging Face TRL (100% Local / Air-Gapped)
# Generated by AI Automated Systems Storefront Optimization Fabric

import torch
from unsloth import FastLanguageModel
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments

# 1. Load Model with 4-bit QLoRA Quantization
max_seq_length = {context_length}
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{model_key}",
    max_seq_length=max_seq_length,
    load_in_4bit=True,
    dtype=None,  # Auto-detects Float16 / Bfloat16
)

# 2. Attach Sovereign LoRA Adapters
model = FastLanguageModel.get_peft_model(
    model,
    r={lora_r},
    target_modules={targets_str},
    lora_alpha={lora_alpha},
    lora_dropout=0.0,  # Unsloth supports 0 dropout with optimal regularization
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

# 3. Format Instruction Dataset
# Format expected: JSONL with {{"instruction": "...", "input": "...", "output": "..."}}
def formatting_prompts_func(examples):
    instructions = examples["instruction"]
    inputs = examples.get("input", [""] * len(instructions))
    outputs = examples["output"]
    texts = []
    for inst, inp, out in zip(instructions, inputs, outputs):
        prompt = f"<|im_start|>system\\nYou are a sovereign domain specialist AI.<|im_end|>\\n<|im_start|>user\\n{{inst}}\\n{{inp}}<|im_end|>\\n<|im_start|>assistant\\n{{out}}<|im_end|>"
        texts.append(prompt)
    return {{"text": texts}}

dataset = load_dataset("json", data_files="dataset.jsonl", split="train")
dataset = dataset.map(formatting_prompts_func, batched=True)

# 4. Supervised Fine-Tuning Trainer Configuration
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=max_seq_length,
    dataset_num_proc=2,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_ratio=0.05,
        num_train_epochs={epochs},
        learning_rate={learning_rate},
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=3407,
        output_dir="{output_dir}",
        save_strategy="epoch",
    ),
)

# 5. Execute Training & Export GGUF / LoRA
print("Starting sovereign air-gapped training pass...")
trainer_stats = trainer.train()

# Save LoRA adapter & merged 16bit / GGUF model for local Ollama / vLLM deployment
model.save_pretrained("{output_dir}")
tokenizer.save_pretrained("{output_dir}")
print(f"✅ Adapter saved successfully to {output_dir}")
'''
