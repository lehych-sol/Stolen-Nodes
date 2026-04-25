# darkHUB Seedream 4.5

`darkHUB Seedream 4.5` is a focused ComfyUI custom node package for the official Freepik Seedream v4.5 APIs.

This package intentionally keeps the scope small and stable:

- `Seedream v4.5 Generate`
- `Seedream v4.5 Edit`
- one clean node in ComfyUI search: `darkHUB Seedream 4.5`

## Features

- Single node with two modes:
  - `Seedream v4.5 Generate`
  - `Seedream v4.5 Edit`
- Native ComfyUI `IMAGE` output compatible with `Save Image`
- Up to 5 reference image inputs for edit mode
- Automatic reference-image fallback flow for edit mode:
  - raw base64
  - data URI
  - temporary public URL fallback
- Freepik task metadata saved locally for successful and failed runs
- Seed control supports ComfyUI's built-in `fixed / increment / decrement / randomize`
- Optional Firebase admin sync starter under `darken/` for consent-based monitoring

## Requirements

- ComfyUI
- Python 3.10+
- Freepik Developers API key
- `requests>=2.31.0`

## Installation

### Option 1: Manual

Clone or copy this folder into your ComfyUI `custom_nodes` directory:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Cyber05CC/ComfyUI-darkHUB-Seedream4.5.git
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Restart ComfyUI.

### Option 2: Comfy Registry / ComfyUI-Manager

After this repository is published to the Comfy Registry, users will be able to install it from ComfyUI-Manager or with:

```bash
comfy node install darkhub-seedream45
```

## API Key

Recommended environment variable:

### Windows

```powershell
$env:FREEPIK_API_KEY="your_api_key_here"
```

### Linux / macOS

```bash
export FREEPIK_API_KEY="your_api_key_here"
```

You can also paste the key directly into the node, but that may store it in your workflow JSON.

## Node Inputs

### Required

- `mode`
- `prompt`
- `negative_prompt`
- `api_key`
- `aspect_ratio`
- `seed`
- `enable_safety_checker`
- `timeout_seconds`
- `poll_interval_seconds`
- `filename_prefix`
- `webhook_url`

### Optional

- `reference_image_1`
- `reference_image_2`
- `reference_image_3`
- `reference_image_4`
- `reference_image_5`

`Seedream v4.5 Edit` requires at least one connected `reference_image_*` input.

## Outputs

- `images`
- `task_id`
- `status`
- `image_urls_json`
- `saved_paths_json`
- `task_json`
- `metadata_path`
- `summary`

## Saved Files

Outputs are saved under:

```text
ComfyUI/output/darkHUB-Seedream-4.5/
```

Each run also saves a metadata JSON file containing:

- request summary
- task response
- saved asset paths
- provider failure context when available

## Example Workflows

Included examples:

- [Generate Workflow](examples/darkhub-seedream45-generate.json)
- [Edit Workflow](examples/darkhub-seedream45-edit.json)

## Optional Firebase Admin Feed

This repository also includes an optional Firebase starter in:

```text
darken/
```

It adds:

- a Spark-compatible Firebase Hosting admin panel
- direct Firestore event logging from the Python node
- Firestore rules for admin-only reads
- `node-sync.example.json` for local sync setup

See:

- [darken/README.md](darken/README.md)

Use this only with clear user notice and consent.

## Freepik Notes

- If Freepik returns `HTTP 429`, your free trial or paid API credits have been exhausted.
- Some provider-side failures may still happen after task creation due to moderation or internal validation on Freepik's side.
- Edit mode reliability is highest when prompts are clear and reference images are clean and supported.

## Publishing

This repository is prepared for both GitHub and Comfy Registry publishing.

### 1. GitHub

Create the repository and push:

```bash
git init
git add .
git commit -m "Initial publish-ready package"
git branch -M main
git remote add origin https://github.com/Cyber05CC/ComfyUI-darkHUB-Seedream4.5.git
git push -u origin main
```

If your GitHub repository path is different, update these files before publishing:

- `pyproject.toml`
- `.github/workflows/publish_action.yml`

Before a public release, review `darken/` carefully:

- keep `darken/node-sync.json` local unless users have clearly opted in
- keep `.firebaserc` local to your machine
- public repositories should usually include only examples such as `node-sync.example.json`

### 2. Comfy Registry

1. Create a publisher in the Comfy Registry
2. Generate a Registry publishing API key
3. Add `REGISTRY_ACCESS_TOKEN` as a GitHub Actions secret
4. Push a new version in `pyproject.toml`

This repository already includes a publish workflow at:

```text
.github/workflows/publish_action.yml
```

## License

MIT
