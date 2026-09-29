# ITCS355 Lab 1
# `make reproduce` is the one command a grader runs. Keep it working.

SHELL := /bin/bash
IMAGE ?= itcs355-lab1
TAG   ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
PLATFORM ?= linux/amd64
SEED ?= 20260101

.PHONY: help setup cloud-check data test portability-audit train image image-push reproduce verify clean teardown \
        tune compare reload-check serve serve-image loadtest deploy smoke drift inject-drift pipeline cost swap-check llm-eval llm-gate \
        scan-secrets

help:
	@grep -E "^[a-zA-Z_-]+:.*?## .*$$" $(MAKEFILE_LIST) | awk -F":.*?## " "{printf \"  %-20s %s\\n\", \$$1, \$$2}"

setup: ## Install dependencies and print environment status
	python -m pip install --upgrade pip
	pip install -r requirements.txt
	@echo "environment ok"

cloud-check: ## Resolve the eight capability slots
	python scripts/cloud_check.py

data: ## Generate the default dataset (deterministic)
	python scripts/make_dataset.py --seed $(SEED)

test: ## Run data contract and split property tests
	pytest -q tests/

portability-audit: ## Fail if provider strings leak into src/
	python scripts/portability_audit.py

train: ## Train locally, outside the container
	python -m src.train --seed $(SEED) --metrics-out reports/metrics.json

image: ## Build the training image for linux/amd64
	docker buildx build --platform $(PLATFORM) -t $(IMAGE):$(TAG) --load .

image-push: image ## Push to CONTAINER_REGISTRY via your adapter
	python -c "from src import config; from cloudlayer.factory import get_adapter; \
	print(get_adapter(config.load()).push_image(\"$(IMAGE):$(TAG)\"))"

reproduce: data image ## THE ONE COMMAND. Grader runs this.
	docker run --rm \
	  -v "$$PWD/data:/app/data:ro" \
	  -v "$$PWD/reports:/app/reports" \
	  -e MLFLOW_TRACKING_URI=sqlite:////app/reports/mlflow.db \
	  $(IMAGE):$(TAG) --seed $(SEED) --metrics-out /app/reports/metrics.json

verify: ## Check the produced metric against the README claim
	python scripts/verify_metric.py

scan-secrets: ## Scan Git history for credential-shaped values
	python scripts/scan_secrets.py

teardown: ## Delete every resource tagged course=itcs355 for this lab
	python -c "from src import config; from cloudlayer.factory import get_adapter; \
	cfg=config.load(); print(get_adapter(cfg).teardown(cfg.tags(1)))"

clean: ## Remove local artifacts
	rm -rf mlruns mlartifacts mlflow.db reports/metrics.json .pytest_cache

# --- Lab 2 -------------------------------------------------------------------
tune: ## Budgeted hyperparameter study (>=12 trials)
	python -m src.tune --trials 12 --budget-thb 150

compare: ## Rank runs by metric and by cost per point
	python scripts/compare_runs.py --experiment itcs355-lab2

reload-check: ## Load the registered model by version and score rows
	python scripts/reload_check.py --name $(MODEL_REGISTRY_NAME) --version $(VERSION)

# --- Lab 3 -------------------------------------------------------------------
serve: ## Run the inference service locally on :8080
	python scripts/export_model.py --out reports/model.joblib
	MODEL_PATH=reports/model.joblib MODEL_VERSION=local uvicorn service.app:app --port 8080

serve-image: ## Build the serving image
	docker buildx build --platform $(PLATFORM) -f service/Dockerfile.serve -t itcs355-serve:$(TAG) --load .

deploy: ## Deploy the serving image to Azure Container Apps
	MLFLOW_TRACKING_URI="azureml://southeastasia.api.azureml.ms/mlflow/v1.0/subscriptions/034c53a8-f544-4cf7-8fab-ede2e693962a/resourceGroups/itcs355-6688020/providers/Microsoft.MachineLearningServices/workspaces/itcs3556688020-ml" \
	MODEL_VERSION=2 \
	python -c "from cloudlayer.factory import get_adapter; from src import config; cfg=config.load(); print(get_adapter(cfg).deploy('itcs3556688020.azurecr.io/itcs355@sha256:21d9f3cbded6cdf3023acc5f9057a89f9009d518f6227b6d7fc8293817cbe923', 'itcs355-predict', '0.25:0.5Gi'))"

smoke: ## Smoke test the deployed prediction endpoint
	python -c "from cloudlayer.factory import get_adapter; from src import config; import subprocess; cfg=config.load(); url=subprocess.check_output(['az','containerapp','show','--name','itcs355-predict','--resource-group',cfg.project_id,'--query','properties.configuration.ingress.fqdn','-o','tsv'], text=True).strip(); payload={'temp_c':78.4,'vibration_mm_s':3.1,'pressure_kpa':315.2,'hours_since_service':4200.0,'load_pct':68.0,'ambient_humidity':55.0}; adapter=get_adapter(cfg); print(adapter.invoke('https://'+url,payload))"

loadtest: ## Load test at three concurrency levels
	@for vus in 1 10 50; do \
	  echo "=== $$vus VUs ==="; \
	  k6 run -e TARGET=$(TARGET) -e VUS=$$vus loadtest/k6.js || true; \
	done

# --- Lab 4 -------------------------------------------------------------------
inject-drift: ## Shift a feature's distribution on purpose
	python scripts/inject_drift.py --feature temp_c --mode shift --magnitude 6

drift: ## Score drift against the reference window
	python -m monitoring.drift --current data/current.csv

# --- Lab 5 -------------------------------------------------------------------
pipeline: ## Compile pipeline/pipeline.yaml for your provider
	python -c "from cloudlayer.pipelines import compile_for; from src import config; \
	compile_for(config.load().provider)"

llm-eval: ## Run the LLM golden set against recorded responses (offline, free)
	python scripts/llm_eval.py --out reports/llm_eval-baseline.json

llm-gate: ## Prove the gate fails on a degraded set — expected to exit non-zero
	python scripts/llm_eval.py --out reports/llm_eval-baseline.json >/dev/null
	python scripts/llm_eval.py --responses evals/fixtures/triage-regressed.jsonl \
	  --out reports/llm_eval.json --baseline reports/llm_eval-baseline.json

cost: ## Build the cost report
	python scripts/cost_report.py --estimate $(EST) --actual $(ACT) --rps $(RPS) --instance $(INSTANCE)

swap-check: ## Prove the portability seam against a second provider
	python scripts/portability_swap_check.py --second-provider $(SECOND)
