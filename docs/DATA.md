# Datasets

A dataset is a fixed set of transmission-line cross-sections with the parameters the 2D field
solver (`pipeline/fields.py`) computes for each: the training corpus for the learned
impedance surrogate. It lives as `datasets` / `dataset_shards` rows plus objects under
`datasets/<id>/` in the store, and is generated through the job queue so a 5000-sample run is
four shard jobs a worker chews through, not one script that has to stay alive.

## Sampler (`data.sample`, `SAMPLER_VERSION = cross-section-0.1`)

| field | range | why |
|---|---|---|
| `w` trace width | 0.1 - 2.0 mm, log-uniform | 4 mil signal traces up to wide power traces; log so the narrow end, where impedance moves fastest, is not starved |
| `h` dielectric height | 0.08 - 1.6 mm, log-uniform | thin prepreg on a 4-layer board up to a full 1.6 mm 2-layer core |
| `t` copper thickness | {0.018, 0.035, 0.070} mm | 0.5, 1, 2 oz, the only foils fabs stock |
| `er` relative permittivity | 3.0 - 4.8 uniform | low-loss laminates through FR-4 |
| `s` gap, coupled pair | 0.1 - 3.0 mm log-uniform, present in half the samples; `None` for a single line | diff pairs and crosstalk need the coupled solve; singles keep the microstrip case dense |

Every sample is drawn from `random.Random(data.shard_seed(seed, shard))` (`seed * 1000 +
shard`) in a fixed order, so a dataset is a pure function of `(sampler_version, seed, shards,
samples_per_shard)`.

## Shard job (`data:shard`, payload `{dataset_id, shard}`)

Loads the dataset row, refuses if its `solver_version` is not the installed
`fields.SOLVER_VERSION`, samples `samples_per_shard` geometries, solves them across
`os.cpu_count()` processes, and writes `datasets/<id>/shard-<NNN>.jsonl`, one object per line:

```json
{"i": 0, "geometry": {"w": 0.31, "h": 0.2, "t": 0.035, "er": 4.1, "s": null},
 "params": {"z0": 51.2, "eps_eff": 3.1, "c_pf_per_m": 115.0,
            "z_even": null, "z_odd": null, "z_diff": null, "coupling": null},
 "solver": "fd2d-0.1", "seconds": 0.42}
```

Then it upserts the `dataset_shards` row (`object_key`, `sha256`, `n_samples`, `seconds`).
Rerunning a shard overwrites the same key and row. Any solver exception fails the whole shard
and marks the dataset `failed`; the worker log carries the traceback.

## Manifest and provenance

`finalize` (run by `scripts/dataset.py wait`, or by hand) refuses while any shard row lacks an
object, then writes `datasets/<id>/manifest.json`:

```
{dataset_id, name, sampler_version, solver_version, seed, n_shards, samples_per_shard,
 n_samples, shards: [{shard, object_key, sha256, n_samples, seconds}], created_at, finalized_at}
```

and sets the dataset row to `ready` with `manifest_key` and `n_samples`. Versions and seed say
what produced the data; the per-shard sha256 says which bytes. `data.load` rejects a shard whose
bytes no longer hash to the manifest's value, so a model trained from a manifest can name the
exact corpus.

## Commands

```
make dataset SHARDS=4 N=1250 SEED=0           # create: prints the dataset id
scripts/dataset.py status ID                  # shard rows and the queue state of its jobs
scripts/dataset.py wait ID [--timeout S]      # poll, then finalize and print the summary
scripts/dataset.py show ID                    # manifest and the first three samples
scripts/dataset.py create --dataset ID --only-shard K   # regenerate one shard
```

A worker (`make worker`) must be running: `create` only enqueues. To regenerate a shard after a
failure or a solver fix, `create --dataset ID --only-shard K` re-enqueues it, then `wait ID`
finalizes again. A solver with a new `SOLVER_VERSION` cannot fill an old dataset's shards; make
a new dataset.

## Training

`scripts/train_surrogate.py --dataset ID` calls `data.load(ID)`, gets every sample as a dict
with the hashes verified, and fits on `geometry -> params`. The `models` row it writes carries
`dataset_id`, closing the chain artifact -> dataset -> sampler/solver versions and seed.

## Caveat

The oracle is a 2D quasi-static finite-difference solver: it gives the per-length capacitance
and the derived `z0`, `eps_eff`, even/odd mode impedances for a uniform cross-section. It knows
nothing about frequency-dependent loss, dispersion, surface roughness, discontinuities or
finite trace length. The surrogate inherits every one of those blind spots; treat its numbers
as impedance-controlled-stackup estimates, not measurements.
