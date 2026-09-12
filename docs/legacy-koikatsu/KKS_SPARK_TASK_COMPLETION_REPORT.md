# KKS Spark Completion Report (2026-07-22)

This report completes the `gpt-5.3-codex-spark` deliverables from
[CODEX_MODEL_TASK_ALLOCATION_KKS.md](CODEX_MODEL_TASK_ALLOCATION_KKS.md):
SP-1 (stratified sampling + acceptance) and SP-2 (contract regression).

Link maintenance (2026-09-12): the following source/test/output paths are historical
evidence locations absent from the MAIN2 tree. They are retained as plain pointers,
not working repository links; this note does not re-run or change the recorded results.

## SP-1: Stratified sampling and acceptance script

### Changes

- Updated `audit_kks_universal_pipeline.py`:
  - `backend/tools/audit_kks_universal_pipeline.py` (historical, absent from MAIN2)
  - Added deterministic sampling options:
    - `--sample-per-stratum` (per-layer sample cap, 0 = full scan)
    - `--sample-seed` (stable selection seed)
  - Added stratum design for sampling:
    - gender from path (`male` / `female`)
    - size bucket (`small` / `medium` / `large`)
  - Added acceptance report fields:
    - `sampling` metadata
    - `issue_clusters` grouped by code with examples

### Stratified run (performance mode)

Command:
```
$env:PYTHONPATH='E:/aiPlay/AI-Knowledge visualization/backend'
.venv\Scripts\python.exe backend/tools/audit_kks_universal_pipeline.py \
  --cards "E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine\UserData\chara" \
  --runtime-root legacy/koikatsu-3d/resources/runtime/runtime-skins \
  --runtime-root legacy/koikatsu-3d/resources/runtime/shared-base/neutral-body \
  --sample-per-stratum 6 \
  --sample-seed 1337 \
  --max-examples 20 \
  --report output/kks-spark-sp1-audit.json
```

- Output report: `output/kks-spark-sp1-audit.json` (historical local artifact)
- Selected cards: 37 of 1530 discovered (7 strata, total sample rate 2.4%)
- Failure clusters in this sample:
  - `card.reference-zero-or-negative` (7)
  - `skin.material-index-range-invalid` (6)

### Full run (compatibility mode)

Command:
```
$env:PYTHONPATH='E:/aiPlay/AI-Knowledge visualization/backend'
.venv\Scripts\python.exe backend/tools/audit_kks_universal_pipeline.py \
  --cards "E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine\UserData\chara" \
  --runtime-root legacy/koikatsu-3d/resources/runtime/runtime-skins \
  --runtime-root legacy/koikatsu-3d/resources/runtime/shared-base/neutral-body \
  --report output/kks-universal-audit-v2.json \
  --max-examples 20
```

- Output report: `output/kks-universal-audit-v2.json` (historical local artifact)
- Failure clusters:
  - `card.reference-zero-or-negative` (345)
  - `skin.material-index-range-invalid` (6)
- `passed` is false in both reports because data includes known invalid reference rows. This is expected for current local dataset and is now clearly clustered.

## SP-2: Contract/API regression

### Test surface covered

- Parse and semantic contracts:
  - `backend/tests/test_koikatsu_card_semantics.py` (historical, absent from MAIN2)
- Index/lookup boundaries:
  - `backend/tests/test_koikatsu_card_index.py` (historical, absent from MAIN2)
  - `backend/tests/test_koikatsu_mod_index.py` (historical, absent from MAIN2)
- Audit unit checks:
  - `backend/tests/test_kks_universal_pipeline_audit.py` (historical, absent from MAIN2)

### Evidence

- SP-2 run command:
```
$env:PYTHONPATH='E:/aiPlay/AI-Knowledge visualization/backend'
.venv\Scripts\python.exe -m pytest backend/tests/test_koikatsu_card_semantics.py backend/tests/test_koikatsu_card_index.py backend/tests/test_koikatsu_mod_index.py backend/tests/test_kks_universal_pipeline_audit.py --junitxml=output\kks-spark-sp2-junit.xml
```
- SP-2 JUnit report:
  - `output/kks-spark-sp2-junit.xml` (historical local artifact)
- Result recorded: 118 passed, 0 failed.

## Handoff notes

- Current accepted failure clusters are data/asset quality issues, not runtime structural regressions:
  - zero/invalid asset ids in card references
  - runtime material index metadata missing in some generated assets (`materials[1] start=None, count=None`)
- Next step before large-scale user-facing testing: normalize these two clusters and rerun SP-1 full mode.
