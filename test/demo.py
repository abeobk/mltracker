"""
MLTracker SDK demo.

Simulates a short training loop — logs scalar metrics and a generated image
each step, then finishes the run.

Run:
    python demo.py

Credentials are read from ~/.mltracker or env vars:
    export MLTRACKER_API_KEY=<your-key>
    export MLTRACKER_HOST=http://localhost:5000   # if running locally
"""
import math
import random
import mltracker as tracker

# ── Init ────────────────────────────────────────────────────────────────────
run = tracker.init(
    project = "demo",
    name    = "training-run",
    config  = {
        "lr":          0.01,
        "epochs":      10,
        "batch_size":  32,
        "optimizer":   "adam",
    },
)
print(f"Started run: {run.name}")

# ── Training loop ────────────────────────────────────────────────────────────
for step in range(20):
    # Simulated metrics
    loss = 1.0 * math.exp(-0.15 * step) + random.uniform(-0.02, 0.02)
    acc  = 1.0 - loss * 0.8 + random.uniform(-0.01, 0.01)
    lr   = 0.01 * math.exp(-0.05 * step)

    run.log({
        "loss": round(loss, 4),
        "acc":  round(min(acc, 1.0), 4),
        "lr":   round(lr, 6),
    }, step=step)

    print(f"  step {step:2d}  loss={loss:.4f}  acc={acc:.4f}")

# ── Finish ───────────────────────────────────────────────────────────────────
run.finish()
print(f"Run finished: {run.name}")
