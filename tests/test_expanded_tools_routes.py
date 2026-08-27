import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_finetuning_optimizer_page():
    r = client.get("/tools/finetuning-optimizer")
    assert r.status_code == 200
    assert "Sovereign Fine-Tuning" in r.text
    assert "LoRA Optimizer" in r.text


def test_finetune_estimate_api():
    r = client.post(
        "/api/tools/finetune-estimate",
        json={
            "model_key": "llama-3.1-8b",
            "method": "qlora_4bit",
            "lora_r": 16,
            "context_length": 4096,
            "dataset_tokens": 1000000,
            "epochs": 3,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "vram_breakdown" in data
    assert "training_estimate" in data
    assert data["vram_breakdown"]["breakdown_gb"]["total_vram"] > 0


def test_finetune_recipe_export_api():
    r = client.post(
        "/api/tools/finetune-recipe-export",
        json={
            "model_key": "llama-3.1-8b",
            "lora_r": 16,
            "lora_alpha": 32,
            "context_length": 4096,
            "learning_rate": 0.0002,
            "epochs": 3,
        },
    )
    assert r.status_code == 200
    assert "FastLanguageModel" in r.text
    assert "SFTTrainer" in r.text


def test_model_benchmarks_page_and_api():
    r = client.get("/tools/model-benchmarks")
    assert r.status_code == 200
    assert "Model Benchmarks" in r.text

    api_r = client.post(
        "/api/tools/benchmark-query",
        json={
            "model_key": "llama-3.3-70b",
            "context_tokens": 8192,
            "concurrent_users": 4,
            "quant_cache": "fp16",
        },
    )
    assert api_r.status_code == 200
    data = api_r.json()
    assert data["status"] == "ok"
    assert data["model_detail"]["parameters_b"] > 70


def test_stack_matrix_page():
    r = client.get("/tools/stack-matrix")
    assert r.status_code == 200
    assert "Sovereign Stack Matrix" in r.text
    assert "Storefront & Telemetry Gateway" in r.text


def test_catalog_category_filtering():
    r_all = client.get("/")
    assert r_all.status_code == 200

    r_filtered = client.get("/?category=Enterprise")
    assert r_filtered.status_code == 200
    assert "Enterprise Suites" in r_filtered.text or "Sentinel" in r_filtered.text


def test_blueprint_json_export():
    # First generate blueprint
    gen_r = client.post(
        "/api/blueprint/generate",
        json={
            "email": "architect@defense-contractor.ca",
            "workload": "finetune_specialist",
            "scale": "medium",
            "compliance": "air_gapped",
        },
    )
    assert gen_r.status_code == 200
    token = gen_r.json()["token"]

    # View page
    page_r = client.get(f"/blueprint/{token}")
    assert page_r.status_code == 200
    assert "Autonomous Domain Fine-Tuning" in page_r.text

    # JSON export
    json_r = client.get(f"/api/blueprint/{token}/json")
    assert json_r.status_code == 200
    assert json_r.json()["client_email"] == "architect@defense-contractor.ca"
