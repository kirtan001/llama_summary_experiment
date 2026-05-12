# EC2 Deployment Plan for Llama Summary Experiment

Last updated: 2026-05-12

This guide explains how to deploy `llama_summary_experiment` on an AWS EC2 instance using Docker Compose. The intended flow is:

```text
GitHub push to main
  -> GitHub Actions builds Docker images
  -> Images are pushed to GitHub Container Registry
  -> EC2 runs docker compose up -d
  -> Compose pulls the images and starts Ollama, API, and UI
```

## 0. Important Free-Tier Reality Check

This project runs a local LLM through Ollama. That is heavier than a normal web app.

AWS Free Tier eligibility depends on when the account was created. AWS changed the EC2 Free Tier model on July 15, 2025. The official EC2 docs say older accounts and newer accounts have different eligible instance types and duration rules. Always trust the AWS Console labels and the Free Tier usage page for your own account.

For this app:

- `t2.micro` / `t3.micro` with 1 GiB RAM is usually too small for Ollama.
- `t3.small` / `t4g.small` with 2 GiB RAM may still struggle with the 3B model, even with swap.
- This deployment uses `llama3.2:3b-instruct-q4_K_M` only.
- For the 3B model, expect to need a larger instance, usually not fully free-tier safe.
- Avoid GPU instances unless your student account explicitly includes credit for them. They are usually not free-tier safe.

Minimum student/free-tier attempt for 3B:

```text
Instance: t3.small or t4g.small, only if AWS marks it Free tier eligible for your account
Storage: 25-30 GiB gp3
Model: llama3.2:3b-instruct-q4_K_M
Swap: 4 GiB
```

More reliable but likely paid option:

```text
Instance: t3.medium, t3.large, or better
Storage: 30+ GiB gp3
Model: llama3.2:3b-instruct-q4_K_M
```

## 1. Current Project Deployment Files

The repo now contains:

```text
.github/workflows/docker-images.yml   # Builds and pushes API/UI images to GHCR
docker-compose.yml                    # EC2 deployment compose file, pulls images
docker-compose.local.yml              # Local development compose file, builds images locally
.env.example                          # EC2 environment template
```

The EC2 compose file starts:

```text
ollama        -> local Ollama server inside Docker network
ollama-pull   -> one-time helper that pulls the selected model
summary-api   -> FastAPI backend, internal API on port 8000, host localhost:8010
summary-ui    -> Streamlit UI, public host port 8510
```

On EC2 you should open only the UI port:

```text
8510/tcp -> your IP address
```

Do not expose Ollama port `11434` publicly. Do not expose API port `8010` publicly unless you have a specific reason.

## 2. Push The Project To GitHub

From your local machine:

```bash
cd d:/pulse_orbital/review/llama_summary_experiment
git status
git add .
git commit -m "Add GHCR Docker deployment for EC2"
git push origin main
```

Then open GitHub:

```text
https://github.com/kirtan001/llama_summary_experiment/actions
```

Wait for the `Build Docker Images` workflow to pass.

The workflow publishes these images:

```text
ghcr.io/kirtan001/llama_summary_experiment-api:latest
ghcr.io/kirtan001/llama_summary_experiment-ui:latest
```

It also builds both CPU architectures:

```text
linux/amd64
linux/arm64
```

That means the same image tag can run on `t3.*` x86 instances or `t4g.*` ARM/Graviton instances.

## 3. Check GitHub Package Visibility

After the workflow finishes, go to the repository page on GitHub and check the Packages section.

If packages are public:

- EC2 can pull them without `docker login`.

If packages are private:

- EC2 must log in to `ghcr.io`.
- Create a GitHub personal access token classic with `read:packages`.
- Do not paste that token into files committed to Git.

Private GHCR login command on EC2:

```bash
read -s CR_PAT
echo "$CR_PAT" | docker login ghcr.io -u kirtan001 --password-stdin
unset CR_PAT
```

When prompted after `read -s CR_PAT`, paste the GitHub token and press Enter.

## 4. Set AWS Budget And Safety Controls First

Before launching EC2, create a budget.

In AWS Console:

```text
Billing and Cost Management
  -> Budgets
  -> Create budget
  -> Cost budget
  -> Monthly budget
  -> Amount: 1 USD or 5 USD
  -> Add email alert at 50%, 80%, and 100%
```

Also check:

```text
Billing and Cost Management
  -> Free Tier
```

And:

```text
EC2
  -> EC2 Dashboard
  -> Free Tier usage box
```

Cost safety rules:

- Run only one instance while testing.
- Use only instance types marked `Free tier eligible` in your account if you want to avoid charges.
- Keep EBS storage at or below the free-tier amount shown in your AWS account.
- Do not allocate an Elastic IP unless you understand the charges.
- Stop or terminate the instance when not using it.
- Delete unattached EBS volumes after terminating instances.

## 5. Launch The EC2 Instance

Open:

```text
AWS Console
  -> EC2
  -> Instances
  -> Launch instance
```

Use these settings.

Name:

```text
llama-summary-experiment
```

AMI:

```text
Ubuntu Server 24.04 LTS
```

Choose an AMI marked Free tier eligible. Ubuntu 22.04 LTS is also fine.

Architecture:

```text
64-bit x86 for t3.*
64-bit Arm for t4g.*
```

Instance type:

```text
Best student/free attempt: t3.small or t4g.small, if marked Free tier eligible
If only micro is free: t3.micro or t2.micro can run Docker, but Ollama will likely fail or be extremely slow
Reliable paid test: t3.medium or larger
```

Key pair:

```text
Create a new key pair
Type: ED25519 or RSA
Format: .pem
Download and keep it safe
```

Network settings:

Create a security group:

```text
SSH
Type: SSH
Port: 22
Source: My IP

Streamlit UI
Type: Custom TCP
Port: 8510
Source: My IP
```

For quick testing you can temporarily use `0.0.0.0/0` for port `8510`, but restrict it to your IP after testing.

Storage:

```text
Root volume: 25-30 GiB
Volume type: gp3
Delete on termination: Yes
```

Then click:

```text
Launch instance
```

## 6. SSH Into The Instance

Find the EC2 public IPv4 address.

From macOS/Linux/Git Bash:

```bash
chmod 400 path/to/your-key.pem
ssh -i path/to/your-key.pem ubuntu@EC2_PUBLIC_IP
```

From PowerShell:

```powershell
ssh -i C:\path\to\your-key.pem ubuntu@EC2_PUBLIC_IP
```

If you choose Amazon Linux instead of Ubuntu, the user is usually:

```text
ec2-user
```

This guide assumes Ubuntu, so commands use the `ubuntu` user.

## 7. Install Docker And Docker Compose Plugin

Run these commands on the EC2 instance:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

Add Docker's apt repository:

```bash
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
```

Install Docker:

```bash
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run hello-world
```

Allow the `ubuntu` user to run Docker:

```bash
sudo usermod -aG docker $USER
newgrp docker
docker version
docker compose version
```

If `newgrp docker` does not work cleanly, log out and SSH back in.

## 8. Add Swap For Small Instances

Small free-tier instances need swap. This helps prevent immediate out-of-memory kills.

Create a 4 GiB swap file:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Check memory:

```bash
free -h
swapon --show
```

Optional lower swappiness:

```bash
echo 'vm.swappiness=20' | sudo tee /etc/sysctl.d/99-llama-summary.conf
sudo sysctl --system
```

Swap is not magic. It can make the app survive, but LLM generation can be slow on a tiny CPU instance.

## 9. Put The Compose Files On EC2

Create an app directory:

```bash
mkdir -p ~/llama_summary_experiment
cd ~/llama_summary_experiment
mkdir -p runs
```

If your GitHub repo is public, download the deployment files:

```bash
curl -fsSLO https://raw.githubusercontent.com/kirtan001/llama_summary_experiment/main/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/kirtan001/llama_summary_experiment/main/.env.example
cp .env.example .env
```

If the repo is private, either clone it with GitHub authentication:

```bash
git clone https://github.com/kirtan001/llama_summary_experiment.git
cd llama_summary_experiment
cp .env.example .env
```

Or copy just these files from your laptop to EC2:

```text
docker-compose.yml
.env.example
```

Then on EC2:

```bash
cp .env.example .env
```

## 10. Edit The EC2 Environment File

Open `.env`:

```bash
nano .env
```

Use:

```text
IMAGE_REGISTRY=ghcr.io
IMAGE_OWNER=kirtan001
IMAGE_REPO=llama_summary_experiment
IMAGE_TAG=latest
OLLAMA_MODEL=llama3.2:3b-instruct-q4_K_M
```

Save and exit:

```text
Ctrl+O
Enter
Ctrl+X
```

## 11. Log In To GHCR If Needed

Skip this section if the GitHub packages are public.

If the images are private:

```bash
read -s CR_PAT
echo "$CR_PAT" | docker login ghcr.io -u kirtan001 --password-stdin
unset CR_PAT
```

Use a classic GitHub token with:

```text
read:packages
```

## 12. Start The App

From the EC2 app directory:

```bash
cd ~/llama_summary_experiment
docker compose pull
docker compose up -d
```

Check containers:

```bash
docker compose ps
```

Watch the model pull:

```bash
docker compose logs -f ollama-pull
```

Watch app logs:

```bash
docker compose logs -f summary-api summary-ui
```

The first run can take several minutes because Ollama must download the model.

## 13. Verify The Deployment

On EC2:

```bash
curl http://127.0.0.1:8010/health
curl -I http://127.0.0.1:8510/_stcore/health
```

Expected API response:

```json
{"status":"healthy","service":"llama-summary-experiment"}
```

Open in your browser:

```text
http://EC2_PUBLIC_IP:8510
```

If it does not open:

- Check the EC2 security group allows inbound TCP `8510`.
- Confirm your current IP matches the security group source.
- Run `docker compose ps`.
- Run `docker compose logs summary-ui`.

## 14. Use The App

1. Open `http://EC2_PUBLIC_IP:8510`.
2. Upload the satellite batch JSON.
3. Keep the model as `llama3.2:3b-instruct-q4_K_M`.
4. Start with a small limit, for example 5 or 10 records.
5. Watch logs and memory.

Useful monitoring commands:

```bash
free -h
df -h
docker stats
docker compose logs -f summary-api
```

If the instance becomes slow, let the current run finish or stop the run from the UI.

## 15. Deploy Future Updates

Local machine:

```bash
git add .
git commit -m "Your change"
git push origin main
```

GitHub Actions:

```text
Wait for Build Docker Images to pass
```

EC2:

```bash
cd ~/llama_summary_experiment
docker compose pull
docker compose up -d
docker compose ps
```

Because `docker-compose.yml` uses `pull_policy: always`, this also works:

```bash
docker compose up -d
```

The explicit `pull` command makes the update easier to see.

## 16. Stop The App Without Deleting Data

Stop containers:

```bash
cd ~/llama_summary_experiment
docker compose down
```

This keeps:

```text
runs/                     # experiment output files
ollama_models volume      # downloaded Ollama models
```

Stop the EC2 instance from AWS Console when not using it:

```text
EC2 -> Instances -> Select instance -> Instance state -> Stop instance
```

Stopped instances do not charge compute, but EBS storage can still count or bill depending on your free-tier usage.

## 17. Full Cleanup

Use this when you are done with the experiment.

On EC2:

```bash
cd ~/llama_summary_experiment
docker compose down
docker system prune -af
```

Delete the Ollama model volume if you do not need downloaded models:

```bash
docker volume ls
docker volume rm llama_summary_experiment_ollama_models
```

In AWS Console:

```text
EC2 -> Instances -> Terminate instance
EC2 -> Volumes -> Delete unattached volumes
EC2 -> Elastic IPs -> Release unused Elastic IPs
EC2 -> Security Groups -> Delete unused test security groups
Billing -> Free Tier -> Check usage
Billing -> Bills -> Confirm no unexpected resources
```

## 18. Troubleshooting

### Docker pull says denied or unauthorized

Cause:

```text
GHCR images are private, or EC2 is not logged in.
```

Fix:

```bash
read -s CR_PAT
echo "$CR_PAT" | docker login ghcr.io -u kirtan001 --password-stdin
unset CR_PAT
docker compose pull
```

Or make the GHCR packages public in GitHub Packages settings.

### GitHub Actions cannot push packages

Check:

```text
Repository Settings
  -> Actions
  -> General
  -> Workflow permissions
  -> Read and write permissions
```

Also check that `.github/workflows/docker-images.yml` has:

```yaml
permissions:
  contents: read
  packages: write
```

### Ollama container exits or model fails

Check logs:

```bash
docker compose logs ollama
docker compose logs ollama-pull
dmesg -T | grep -i "killed process"
```

Common fixes:

- Confirm `.env` uses `OLLAMA_MODEL=llama3.2:3b-instruct-q4_K_M`.
- Add swap.
- Run fewer records.
- Upgrade to a larger instance.

### UI loads but run fails

Check API logs:

```bash
docker compose logs -f summary-api
```

Check Ollama is reachable from the Docker network:

```bash
docker compose exec summary-api python - <<'PY'
import requests
print(requests.get("http://ollama:11434/api/tags", timeout=10).text)
PY
```

### Browser cannot open the UI

Check:

```bash
docker compose ps
curl -I http://127.0.0.1:8510/_stcore/health
```

Then verify:

```text
EC2 security group has inbound TCP 8510 from your IP
You are using http://, not https://
The EC2 public IP did not change after stop/start
```

### Disk is full

Check:

```bash
df -h
docker system df
```

Clean unused Docker data:

```bash
docker system prune -af
```

If models are too large:

```bash
docker compose down
docker volume rm llama_summary_experiment_ollama_models
```

Then confirm `.env` still uses the 3B model before starting again.

## 19. Command Summary

Minimal EC2 command sequence after launching Ubuntu:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker

sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

mkdir -p ~/llama_summary_experiment/runs
cd ~/llama_summary_experiment
curl -fsSLO https://raw.githubusercontent.com/kirtan001/llama_summary_experiment/main/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/kirtan001/llama_summary_experiment/main/.env.example
cp .env.example .env

# Only if GHCR package is private:
# read -s CR_PAT
# echo "$CR_PAT" | docker login ghcr.io -u kirtan001 --password-stdin
# unset CR_PAT

docker compose pull
docker compose up -d
docker compose ps
```

Open:

```text
http://EC2_PUBLIC_IP:8510
```

## 20. References

- AWS EC2 Free Tier usage: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html
- Docker Engine install on Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- GitHub Container Registry authentication: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
- GitHub Packages overview and visibility: https://docs.github.com/en/packages/learn-github-packages/introduction-to-github-packages
- Ollama Linux install and service notes: https://docs.ollama.com/linux
- Ollama FAQ for Docker/GPU/concurrency/memory-related settings: https://docs.ollama.com/faq
