# Held-out evaluation

Per-frame scores behind the paper's held-out table. Each `<zone>-<variant>.json` holds the mean and per-frame PSNR, SSIM (unmasked pixels only) and LPIPS (AlexNet, masked pixels copied from the frame), and `.train.txt` the held-out frame list, training cameras and wall-clock time.

Variants: `full` (all non-held-out frames, background fill and hull carving), `v8` (8 evenly spaced training frames), `nofill` (`--fill-weight 0 --no-carve`), `full-s1` (seed 1).

Reproduce, after the zone has been prepared and posed as in `docs/PHOTOREAL.md`:

```bash
python3 scripts/splat_train.py --scene build/photoreal/racking --out build/eval/racking-full/trained.ply \
  --iters 3000 --holdout 8 --preview-every 0 --save-every 0 --threads 4 --k-max 48 --max-splats 200000
python3 scripts/splat_eval.py --scene build/photoreal/racking --ply build/eval/racking-full/trained.ply \
  --holdout 8 --k-max 48 --out build/eval/racking-full/eval.json
```

Add `--train-views 8`, `--fill-weight 0 --no-carve` or `--seed 1` to the training call for the other variants.
