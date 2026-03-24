# mltracker SDK

Python client for [MLTracker](https://github.com/abeobk/mltracker) — log scalar metrics and images from training scripts.

## Install

```bash
pip install mltracker-0.1.0-py3-none-any.whl
```

## Usage

```python
import mltracker as tracker

run = tracker.init(
    project="mnist",
    name="exp-001",
    config={"lr": 0.001, "epochs": 10},
)

for step in range(100):
    run.log({"loss": 0.5 / (step + 1), "acc": 1 - 0.4 / (step + 1)}, step=step)

run.finish()
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `WANDB_API_KEY` | — | Required. Copy from the MLTracker dashboard. |
| `WANDB_HOST` | `http://localhost:5000` | URL of your MLTracker server. |
