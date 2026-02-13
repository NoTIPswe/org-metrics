# Organization Metrics

This repository contains scripts for computing various engineering metrics for the organization.

## Structure

- One directory per metric (e.g., `commit-message-quality/`)
- One GitHub Actions workflow per metric (in `.github/workflows/`)
- Each metric directory contains:
  - `requirements.in` - Abstract dependencies
  - `requirements.txt` - Pinned dependencies (generated, but committed)
  - `<metric-name>.py` - The Python script that computes the metric

## Adding a New Metric

1. Create a new directory with the metric name
2. Add a `requirements.in` file with your dependencies
3. Generate `requirements.txt`:
   ```bash
   cd <metric-directory>
   pip-compile requirements.in
   ```
4. Create the Python script named `<metric-name>.py`
5. Create a workflow in `.github/workflows/<metric-name>.yml`

## Updating Dependencies

```bash
cd <metric-directory>
pip-compile --upgrade requirements.in
```