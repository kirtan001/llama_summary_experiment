# Llama Summary Experiment Lab

Standalone lab for testing local Llama summaries against satellite evidence batches. This does not modify `shadow_route` or `sat_library`.

## Export A Batch

Run from the repo root with the SAT Library conda environment:

```powershell
cd d:\pulse_orbital\review
conda activate scraper_env
python llama_summary_experiment/tools/export_satellite_batch.py --limit 100 --strategy diverse --output llama_summary_experiment/data/sat_batch_100.json
```

Strategies:

- `diverse`: mix evidence counts and countries.
- `random`: random satellites with evidence.
- `top-evidence`: satellites with the most evidence records.

## Run Locally With Docker

```bash
cd llama_summary_experiment
docker compose -f docker-compose.local.yml up --build
```

The local compose file starts:

- `ollama` on the internal Docker network.
- `summary-api` on `127.0.0.1:8010`.
- `summary-ui` on `http://localhost:8510`.

The `ollama-pull` helper pulls `llama3.2:3b-instruct-q4_K_M`.

## GitHub Actions Docker Images

Pushing to `main` runs `.github/workflows/docker-images.yml` and publishes:

```text
ghcr.io/kirtan001/llama_summary_experiment-api:latest
ghcr.io/kirtan001/llama_summary_experiment-ui:latest
```

The workflow also publishes branch and short SHA tags. Pull requests build the images for validation but do not push them.

## Deploy On EC2

For the full student/free-tier EC2 plan, see [`EC2_DEPLOYMENT_PLAN.md`](EC2_DEPLOYMENT_PLAN.md).

Install Docker and the Compose plugin on the EC2 instance, then put this repo's `docker-compose.yml` and `.env` in one folder. Start from the example:

```bash
cp .env.example .env
```

For the default GitHub repository, `.env.example` already points to the correct images:

```text
IMAGE_REGISTRY=ghcr.io
IMAGE_OWNER=kirtan001
IMAGE_REPO=llama_summary_experiment
IMAGE_TAG=latest
OLLAMA_MODEL=llama3.2:3b-instruct-q4_K_M
```

Then run:

```bash
docker compose up -d
```

The deployment compose file uses `pull_policy: always`, so `docker compose up -d` pulls the latest published API/UI images before starting the containers. If the GHCR package is private, log in on EC2 first:

```bash
docker login ghcr.io -u kirtan001
```

Open TCP port `8510` in the EC2 security group, then visit `http://<EC2_PUBLIC_IP>:8510`.

## Use The UI

1. Open `http://localhost:8510`.
2. Upload the exported JSON batch.
3. Choose model/config.
4. Start the run.
5. Watch logs and progress.
6. Download `results.json`, `results.csv`, `comparison.md`, `run.log`, or `run_bundle.zip`.

Each satellite is summarized independently. The model never receives the full 100-satellite batch in one prompt.

## Output Files

Every run writes:

```text
runs/<run_id>/
  input.json
  config.json
  run.log
  results.jsonl
  results.json
  results.csv
  comparison.md
  errors.json
  run_bundle.zip
```

## Local API Dev

If running without Docker, start Ollama separately, then:

```bash
cd llama_summary_experiment
pip install -r requirements.txt
uvicorn app.api:app --host 0.0.0.0 --port 8010
streamlit run app/ui.py --server.port=8510
```
