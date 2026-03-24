"""
Sine wave test — logs 4 metrics over 200 steps using the WandB Clone SDK.
Run after the server is up: python sinewave_test.py
"""
import math
import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import wandb_clone as wandb

KEY  = os.environ.get("WANDB_API_KEY", "59e28ac3da73af896d90a215155398af795a871cc12ae9a3e0606cf9d524c03c")
HOST = os.environ.get("WANDB_HOST", "http://localhost:5000")

if not KEY:
    print("ERROR: set WANDB_API_KEY environment variable first.")
    print("  Windows: set WANDB_API_KEY=<your-key>")
    print("  Linux/Mac: export WANDB_API_KEY=<your-key>")
    sys.exit(1)

print(f"Connecting to {HOST} ...")
run = wandb.init(
    project="demo",
    name="sinewave-02",
    config={"freq": 1.0, "steps": 200, "note": "sine wave demo"},
    api_key=KEY,
    host=HOST,
)
print(f"Run created: id={run.run_id}  name={run.name}")
print(f"  → to resume later: wandb.resume(project='demo', name='{run.name}', api_key=KEY)")

for step in range(200):
    t = step / 20.0
    metrics = {
        "sin":       math.sin(t),
        "cos":       math.cos(t),
        "loss":      1.0 / (1.0 + 0.05 * step),
        "noisy_sin": math.sin(t) + ((step % 7) - 3) * 0.05,
    }
    if step % 10 == 0:
        arr = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
        metrics["random_img"] = wandb.Image(arr)
    run.log(metrics, step=step)
    if step % 50 == 0:
        print(f"  step {step}/200 ...")
    #delay 100ms
    import time; time.sleep(0.5)

run.finish()
print(f"\nDone. Open {HOST} to view the charts.")
