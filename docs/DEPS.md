# DEPS.md — every third-party dependency and why it is here

Populated by whoever adds a dependency, in the same commit as the manifest change. A row
without a reason is a row nobody can defend in a review. Versions below are what is actually
installed in the lockfiles right now, not the range in the manifest.

Python resolves through `uv` (`pyproject.toml` + `uv.lock`). JS resolves through `pnpm`
(`package.json` + `pnpm-lock.yaml`).

**Rows marked *optional* are not installed by `make install`.** They live in a PEP 735
dependency group (`[dependency-groups] sdxl` in `pyproject.toml`) because exactly one mode
needs them and a plain `uv sync` must not pull 0.72 GB for a stage nobody runs by default:

```
make install        uv sync + pnpm install          (no torch, no SDXL — the default)
make install-sdxl   uv sync --group sdxl + fetch the 6.9 GB weights
```

`uv sync` **prunes**: running `make install` on a machine that already has the group reports
`Would uninstall 33 packages` and removes torch/diffusers — that is the intended meaning of "the
group is not part of the base install", not a bug. `make install-sdxl` puts it back (0.7 s from
the wheel cache here).

Making that stack mandatory already caused one regression nobody would have predicted: with
torch present, `runtime.probe()` selected BiRefNet, which has no implementation, so the
default vision path started refusing on a machine where nothing about vision had changed.
`probe()` now asks `segment.birefnet_available()` before selecting, and `make test` is green
on a checkout with neither the group nor the weights.

## Python

| name | version | licence | reason |
|---|---|---|---|
| fastapi | 0.115.14 | MIT | the API framework fixed by BRIEF.md; gives us OpenAPI for free so mobile/growth can code against `/v1` before it exists |
| pydantic | 2.14.1 | MIT | the contract. VLM output is validated against `ItemAttributes`; `extra="forbid"` is what makes a schema change loud |
| pydantic-settings | 2.16.2 | MIT | env-driven config with no secret defaults; `make test` must pass with zero credentials |
| uvicorn | 0.30.6 | BSD-3-Clause | ASGI server for `make dev`; nothing exotic, ships with FastAPI docs |
| python-dotenv | 1.2.4 | BSD-3-Clause | transitive via pydantic-settings (`env_file=".env"`); listed because a secret-handling dep deserves a row |
| pytest | 8.4.2 | MIT | test runner for `make test`; fixture-based, no plugin sprawl |
| httpx | 0.27.2 | BSD-3-Clause | required by `fastapi.testclient`; also the client the eval harness will use for real provider calls |
| ruff | 0.6.9 | MIT | `make lint` for python. One binary for lint+format, no black/isort/flake8 stack |
| pillow | 11.3.0 | MIT-CMU | image decode/encode for `matte()` (E1-T3): photo bytes and mask PNG in, alpha PNG out. Also the format boundary every later vision stage shares |
| numpy | 2.4.6 | BSD-3-Clause | mask/alpha array math in `matte()` (E1-T3): interior-opaque clamp, Chebyshev band guard. The pixel format `rectify`/`prettify` (E2) will consume |
| onnxruntime | 1.31.1 | MIT | runs `u2net.onnx` for `segment()` (E1-T2) on the CPU execution provider — the reason torch is *not* required for the cheap backend. Transitives: `flatbuffers` 25.12.19 (Apache-2.0), `protobuf` 7.36.2 (BSD-3-Clause) |
| torch *(optional, `make install-sdxl`)* | 2.15.0 | BSD-3-Clause | the tensor runtime behind `mode=SDXL_INPAINT` (E2-T2 round 2). **581 MB installed** on macOS arm64, and imported lazily inside the generative branch only — `import packages.vision.prettify` must not pull it, or `make test` stops working on a machine without the round-2 extras (pinned by `test_importing_prettify_does_not_import_torch`). MPS backend is what makes the step viable at all on this hardware. **Not installing the `sdxl` group costs exactly one thing: `mode=SDXL_INPAINT` refuses with `make install-sdxl` in the message. `segment`, `matte`, `rectify`, `prettify(mode=classical)` and `attrs` are unaffected — and so is `make test`, which skips the three weight-dependent tests and runs the refusal path instead.** It also removes a trap: with torch present on a machine whose `runtime.probe()` rule 3 was written before BiRefNet existed, the *default* vision path used to break. That rule is fixed, but a base install cannot trip over it at all |
| diffusers *(optional, `make install-sdxl`)* | 0.42.1 | Apache-2.0 | `StableDiffusionXLInpaintPipeline`: SDXL inpainting, the 9-channel masked-latent UNet input, and the scheduler wiring. Hand-rolling that would be several hundred lines of the least interesting code in the repo |
| transformers *(optional, `make install-sdxl`)* | 5.22.1 | Apache-2.0 | **transitive in practice** (diffusers needs `CLIPTextModel` with projection + `CLIPTextModelWithProjection` for SDXL's two text encoders), pinned explicitly because a diffusers release can widen it silently and the text encoder is a hard load-time dependency of the pipeline |
| accelerate *(optional, `make install-sdxl`)* | 1.15.0 | Apache-2.0 | device placement and the low-cpu-mem loading path `from_pretrained` uses for the 5.1 GB UNet; without it diffusers falls back to a slower loader that peaks higher in RAM, which matters at 24 GB unified |
| safetensors *(optional, `make install-sdxl`)* | 0.8.0 | Apache-2.0 | the weight format the pinned files are in (`*.fp16.safetensors`) and what `use_safetensors=True` requires. Also the reason a corrupt download fails loudly at load instead of unpickling garbage |
| huggingface-hub *(optional, `make install-sdxl`)* | 2.4.0 | Apache-2.0 | `snapshot_download` for `make fetch-sdxl`, with the revision pinned and `allow_patterns` narrowed to the 18 fp16 files. Imported **only** inside `fetch_sdxl_weights()` — the run-time path passes `local_files_only=True` and never needs the Hub client. Transitive: `hf-xet` 1.8.0 (Apache-2.0), `tokenizers` 0.23.3 (Apache-2.0), `regex`, `requests`, `tqdm`, `filelock`, `fsspec`, `jinja2`, `sympy`, `networkx`, `psutil` |

## Hosted services (no package installed)

| name | version / rate | licence / terms | reason |
|---|---|---|---|
| DeepSeek V4.1 Flash (`deepseek-flash`) | `DeepSeek-V4.1-Flash-1017`; off-peak $0.15/M input (cache miss), $0.60/M output; peak 2× (ADR-006) | hosted API under the DeepSeek Open Platform Terms of Service + Privacy Policy; no redistributed weights, so no model-weights licence | the E3 attributes VLM. Chosen by Bert 2026-11-04. Called through the existing lazy `httpx` client; no SDK added. Personal data is processed/stored in the PRC per the privacy policy — legal items OPEN in ADR-006 must be closed before seller photos are sent. **Measured `$/item` is not yet available**: token counts are recorded in `StageMeta.notes` on the first real call; until then `cost_usd` is `None`. |

## Model weights (not packages)

| name | size / pin | licence | reason |
|---|---|---|---|
| U²-Net `u2net.onnx` | 176 MB, sha256 `8d10d2f3…b491` (full pin in `packages/vision/segment.py`) | Apache-2.0 (Xuebin Qin, U-2-Net) redistributed by rembg (MIT) | garment segmentation for `segment()` (E1-T2). Not a package: `make fetch-models` puts it in `~/.cache/snaplist/models/`, it is never committed, and the stage refuses to run (rather than downloading) when it is absent. **`$/item` = 0.00 provider spend** — local CPU, 0.73 s inference measured 2026-10-28 on this machine. Machine-attributed cost (electricity, laptop wear) is *not* measured and is not claimed. |
| SDXL inpainting `stable-diffusion-xl-1.0-inpainting-0.1` | **6.94 GB** (18 files, fp16 variant only — the fp32 set is another ~13 GB and produces identical output), revision pinned to commit **`115134f363124c53c7d878647567d04daf26e41e`** in `packages/vision/prettify.py::SDXL_REVISION`. Per-file byte counts are pinned in `SDXL_REQUIRED_FILES`; the loader refuses a tree that does not match. Path: `~/.cache/snaplist/models/sdxl-inpaint-0.1/` (override with `SNAPLIST_SDXL_DIR`, or `SNAPLIST_MODEL_DIR` for the shared base dir). Fetched **only** by `make fetch-sdxl`. | **CreativeML Open RAIL++-M** — *not* an OSI licence. Commercial use **is** allowed. It is a **use-restricted** licence and the restrictions travel with the weights: no use for unlawful, defamatory, harassing, or deliberately harmful/deceptive purposes; redistribution of the model or a derivative **must pass the same restrictions on** (RAIL §5, "must include … the same use restrictions"). None of that restricts the *outputs* the way the old OpenRAIL image licences were sometimes misread; outputs are not covered by a downstream sharing obligation. Flagged to legal (Bert) rather than assumed acceptable: `docs/ADR` has no entry yet because the wire shape did not change, and the licence note lives here + in `HANDOFF-vision.md`. | the round-2 generative step for `prettify(mode=SDXL_INPAINT)` (E2-T2). Runs locally on MPS at fp16, **~38–43 s warm per 480 px fixture** (measured, see `HANDOFF-vision.md`) — that is the dominant `$/item` driver: no provider spend, but ~40 s of one machine per image. **Quality finding: it does not remove creases, it replaces them with scratch-like lines.** Documented as a known failure, not hidden. |

## JavaScript / TypeScript

| name | version | licence | reason |
|---|---|---|---|
| zod | 3.23.8 | MIT | the zod mirror of `ItemAttributes` in `packages/shared`, and the only runtime validation mobile trusts |
| typescript | 5.5.4 (`packages/shared`) | Apache-2.0 | compiles `@snaplist/shared` and doubles as its linter (`tsc --noEmit`) |
| typescript | 5.3.3 (`apps/mobile`) | Apache-2.0 | pinned by Expo SDK 51's own template; do not bump independently of Expo |
| @types/node | 20.19.43 | MIT | types for `node:test`, `node:assert` and `process` in the shared-package tests |
| expo | 51.0.39 | MIT | the mobile framework fixed by BRIEF.md (SDK 51 = RN 0.74, the last LTS-ish line we can pin confidently) |
| expo-status-bar | 1.12.1 | MIT | status bar styling on the scaffold screen; comes from the Expo template |
| react | 18.2.0 | MIT | peer of React Native 0.74 |
| react-native | 0.74.5 | MIT | the renderer; version dictated by Expo SDK 51 |
| @types/react | 18.2.79 | MIT | type checking for the sole `.tsx` file |

## Not dependencies, on purpose

| thing | why not |
|---|---|
| eslint / prettier | `tsc --noEmit` plus `ruff` already fail the build on real defects. A formatter war is not a v1 problem. Revisit if the diff noise gets in the way. |
| jest / vitest | `node --test` is in the runtime and needs no config or transform for our compiled output |
| celery / redis | explicitly excluded by BRIEF.md; the `jobs` table does this |
| modal | installed in the Modal image, not in the API image. `infra/modal_app.py` is never imported by the test suite |
| torch / diffusers / SAM weights | belong to E1–E4. When they land they must be added here **with** their measured `$/item` impact. Two have landed: the U²-Net weights (onnxruntime, no torch) and, for E2-T2 round 2, the whole `torch`/`diffusers`/`transformers`/`accelerate`/`safetensors` stack (**≈0.72 GB** of site-packages: torch 581 MB, transformers 55 MB, diffusers 29 MB, sympy 29 MB, tokenizers 9.4 MB, networkx 7.8 MB, hf-xet 7.1 MB, huggingface_hub 3.5 MB, the rest <2 MB each) plus the 6.94 GB SDXL inpainting weights. BiRefNet and SAM 2.1 remain blocked, and `segment()` says so instead of downgrading silently. |

## How to add a row

1. Add the dependency to the correct manifest (`pyproject.toml` or the package's `package.json`).
2. Install so the lockfile updates (`uv sync` / `pnpm install`).
3. Add the row here with the version that actually resolved, and a reason a stranger can audit.
4. If the licence is not MIT/BSD/Apache-2.0/ISC, flag it to the legal role before merging.
