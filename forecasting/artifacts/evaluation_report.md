# Forecast Model Evaluation Report

Prophet beats the best baseline on **31 of 74** series (42%) overall.

**Breakdown by metric:**

- `enrollment_count`: 31 of 37 (84%)
- `graduation_count`: 0 of 37 (0%)

**Interval calibration:** 0 of 222 Prophet walk-forward folds (0%) got a genuine MCMC-calibrated 80% interval; the rest fell back to a disclosed MAP-only approximation (training window below `MCMC_MIN_TRAIN_POINTS`, or MCMC sampling diverged -- see `fit_prophet`/`IntervalCalibration`). Coverage figures below apply to whichever interval each fold actually got.

| Program | College | Metric | Prophet MAE | Prophet RMSE | 80% Interval Coverage | Mean Interval Width | Normalized Width | Interval Calibration | Naive MAE | Hist. Avg MAE | Seasonal Naive MAE | Count Model MAE | Best Baseline MAE | Diff (Prophet - Baseline) | Prophet R² | Beats Baseline? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CICT-BSDS | CICT | enrollment_count | 3.29 | 4.00 | 0/3 | 0.86 | 0.01 | 0/3 MCMC | 28.00 | 67.75 | 61.33 | n/a | 28.00 | -24.71 | 0.984 | ✅ |
| CICT-BSDS | CICT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-DB | CICT | enrollment_count | 7.55 | 8.54 | 0/3 | 1.44 | 0.01 | 0/3 MCMC | 40.33 | 94.27 | 88.00 | n/a | 40.33 | -32.78 | 0.967 | ✅ |
| CICT-BSIT-DB | CICT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-NET | CICT | enrollment_count | 7.16 | 7.80 | 0/3 | 1.44 | 0.01 | 0/3 MCMC | 33.00 | 79.87 | 71.67 | n/a | 33.00 | -25.84 | 0.954 | ✅ |
| CICT-BSIT-NET | CICT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-WEB | CICT | enrollment_count | 8.46 | 8.52 | 0/3 | 1.25 | 0.00 | 0/3 MCMC | 43.67 | 108.64 | 98.67 | n/a | 43.67 | -35.21 | 0.969 | ✅ |
| CICT-BSIT-WEB | CICT | graduation_count | 0.52 | 0.66 | 1/3 | 0.19 | 0.58 | 0/3 MCMC | 0.67 | 0.40 | 0.33 | 1.33 | 0.33 | +0.19 | -0.978 | ⚠️ NO |
| CMBT-BSBA-BPO | CMBT | enrollment_count | 14.33 | 14.90 | 0/3 | 4.71 | 0.04 | 0/3 MCMC | 16.00 | 49.23 | 42.00 | n/a | 16.00 | -1.67 | 0.299 | ✅ |
| CMBT-BSBA-BPO | CMBT | graduation_count | 0.33 | 0.58 | 2/3 | 0.00 | 0.00 | 0/3 MCMC | 0.33 | 0.33 | 0.33 | 0.33 | 0.33 | +0.00 | -0.500 | ⚠️ NO |
| CMBT-BSBA-ECON | CMBT | enrollment_count | 6.16 | 7.12 | 1/3 | 5.03 | 0.04 | 0/3 MCMC | 19.00 | 41.03 | 36.33 | n/a | 19.00 | -12.84 | 0.856 | ✅ |
| CMBT-BSBA-ECON | CMBT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-FM | CMBT | enrollment_count | 11.25 | 14.27 | 0/3 | 5.84 | 0.03 | 0/3 MCMC | 34.67 | 87.84 | 79.67 | n/a | 34.67 | -23.41 | 0.881 | ✅ |
| CMBT-BSBA-FM | CMBT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-HRM | CMBT | enrollment_count | 10.30 | 12.97 | 1/3 | 1.47 | 0.01 | 0/3 MCMC | 30.00 | 81.14 | 74.33 | n/a | 30.00 | -19.70 | 0.878 | ✅ |
| CMBT-BSBA-HRM | CMBT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-MM | CMBT | enrollment_count | 2.11 | 2.89 | 0/3 | 0.69 | 0.01 | 0/3 MCMC | 27.00 | 56.39 | 51.67 | n/a | 27.00 | -24.89 | 0.989 | ✅ |
| CMBT-BSBA-MM | CMBT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSENTREP | CMBT | enrollment_count | 11.23 | 12.05 | 0/3 | 2.78 | 0.02 | 0/3 MCMC | 23.00 | 60.33 | 52.67 | n/a | 23.00 | -11.77 | 0.733 | ✅ |
| CMBT-BSENTREP | CMBT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSHM | CMBT | enrollment_count | 5.44 | 6.07 | 0/3 | 0.60 | 0.00 | 0/3 MCMC | 23.00 | 64.37 | 58.33 | n/a | 23.00 | -17.56 | 0.952 | ✅ |
| CMBT-BSHM | CMBT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSTM | CMBT | enrollment_count | 3.63 | 3.99 | 0/3 | 1.07 | 0.01 | 0/3 MCMC | 14.67 | 38.48 | 35.33 | n/a | 14.67 | -11.04 | 0.953 | ✅ |
| CMBT-BSTM | CMBT | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COA-BSARCH | COA | enrollment_count | 10.46 | 13.86 | 0/3 | 8.50 | 0.06 | 0/3 MCMC | 30.33 | 67.70 | 62.00 | n/a | 30.33 | -19.87 | 0.821 | ✅ |
| COA-BSARCH | COA | graduation_count | 0.63 | 0.72 | 1/3 | 0.42 | 0.63 | 0/3 MCMC | 1.00 | 0.68 | 0.33 | 1.40 | 0.33 | +0.29 | -1.324 | ⚠️ NO |
| COA-CERT-BUT | COA | enrollment_count | 2.91 | 3.51 | 0/3 | 1.09 | 0.08 | 0/3 MCMC | 4.33 | 2.23 | 1.00 | n/a | 1.00 | +1.91 | -1.644 | ⚠️ NO |
| COA-CERT-BUT | COA | graduation_count | 3.56 | 4.23 | 1/3 | 2.49 | 0.93 | 0/3 MCMC | 3.33 | 1.72 | 0.67 | 5.24 | 0.67 | +2.90 | -5.189 | ⚠️ NO |
| COA-CERT-CADD | COA | enrollment_count | 6.97 | 7.86 | 0/3 | 1.96 | 0.08 | 0/3 MCMC | 3.33 | 7.29 | 5.33 | n/a | 3.33 | +3.64 | -13.648 | ⚠️ NO |
| COA-CERT-CADD | COA | graduation_count | 3.50 | 3.63 | 0/3 | 1.17 | 0.21 | 0/3 MCMC | 2.67 | 3.30 | 3.00 | 4.28 | 2.67 | +0.84 | -0.382 | ⚠️ NO |
| COA-CERT-DRAFT | COA | enrollment_count | 14.14 | 16.31 | 0/3 | 5.27 | 0.13 | 0/3 MCMC | 9.00 | 9.12 | 6.67 | n/a | 6.67 | +7.47 | -12.147 | ⚠️ NO |
| COA-CERT-DRAFT | COA | graduation_count | 6.07 | 7.30 | 0/3 | 1.50 | 0.15 | 0/3 MCMC | 2.67 | 4.07 | 5.00 | 13.33 | 2.67 | +3.41 | -11.606 | ⚠️ NO |
| COC-BSCRIM | COC | enrollment_count | 16.06 | 25.25 | 0/3 | 1.18 | 0.00 | 0/3 MCMC | 64.33 | 151.51 | 138.00 | n/a | 64.33 | -48.28 | 0.869 | ✅ |
| COC-BSCRIM | COC | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COE-BSCE | COE | enrollment_count | 17.82 | 21.54 | 1/3 | 6.61 | 0.04 | 0/3 MCMC | 30.33 | 73.31 | 68.67 | n/a | 30.33 | -12.51 | 0.624 | ✅ |
| COE-BSCE | COE | graduation_count | 0.96 | 1.25 | 0/3 | 0.37 | 1.12 | 0/3 MCMC | 0.33 | 0.52 | 1.00 | 1.94 | 0.33 | +0.63 | -6.007 | ⚠️ NO |
| COE-BSEE | COE | enrollment_count | 5.73 | 7.58 | 0/3 | 1.73 | 0.01 | 0/3 MCMC | 28.00 | 65.70 | 60.00 | n/a | 28.00 | -22.27 | 0.940 | ✅ |
| COE-BSEE | COE | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COE-BSME | COE | enrollment_count | 13.71 | 17.27 | 0/3 | 1.73 | 0.01 | 0/3 MCMC | 17.00 | 48.13 | 43.33 | n/a | 17.00 | -3.29 | 0.298 | ✅ |
| COE-BSME | COE | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BECED | COED | enrollment_count | 2.82 | 2.88 | 0/3 | 1.04 | 0.01 | 0/3 MCMC | 10.67 | 29.23 | 25.67 | n/a | 10.67 | -7.85 | 0.941 | ✅ |
| COED-BECED | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BEED | COED | enrollment_count | 5.79 | 6.76 | 0/3 | 2.26 | 0.02 | 0/3 MCMC | 20.00 | 49.48 | 42.67 | n/a | 20.00 | -14.21 | 0.886 | ✅ |
| COED-BEED | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BPE | COED | enrollment_count | 10.81 | 11.81 | 0/3 | 2.78 | 0.04 | 0/3 MCMC | 14.33 | 31.46 | 30.67 | n/a | 14.33 | -3.53 | 0.531 | ✅ |
| COED-BPE | COED | graduation_count | 1.12 | 1.12 | 0/3 | 0.50 | 1.49 | 0/3 MCMC | 0.67 | 0.46 | 0.67 | 1.48 | 0.46 | +0.66 | -4.681 | ⚠️ NO |
| COED-BSED-ENG | COED | enrollment_count | 17.45 | 20.06 | 1/3 | 3.94 | 0.03 | 0/3 MCMC | 17.67 | 37.39 | 32.00 | n/a | 17.67 | -0.21 | -0.429 | ✅ |
| COED-BSED-ENG | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-FIL | COED | enrollment_count | 2.68 | 2.69 | 0/3 | 0.41 | 0.01 | 0/3 MCMC | 11.33 | 25.72 | 24.00 | n/a | 11.33 | -8.65 | 0.954 | ✅ |
| COED-BSED-FIL | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-MATH | COED | enrollment_count | 6.94 | 8.98 | 1/3 | 2.30 | 0.03 | 0/3 MCMC | 12.00 | 27.22 | 25.67 | n/a | 12.00 | -5.06 | 0.496 | ✅ |
| COED-BSED-MATH | COED | graduation_count | 0.48 | 0.63 | 1/3 | 0.19 | 0.28 | 0/3 MCMC | 0.33 | 0.60 | 0.67 | 1.00 | 0.33 | +0.15 | -0.785 | ⚠️ NO |
| COED-BSED-PHYS | COED | enrollment_count | 2.20 | 2.89 | 1/3 | 0.78 | 0.02 | 0/3 MCMC | 3.67 | 11.48 | 9.67 | n/a | 3.67 | -1.47 | 0.588 | ✅ |
| COED-BSED-PHYS | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-SCI | COED | enrollment_count | 2.73 | 3.03 | 0/3 | 1.43 | 0.02 | 0/3 MCMC | 9.33 | 23.47 | 21.33 | n/a | 9.33 | -6.60 | 0.925 | ✅ |
| COED-BSED-SCI | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-SS | COED | enrollment_count | 6.74 | 7.18 | 0/3 | 0.65 | 0.01 | 0/3 MCMC | 10.00 | 19.97 | 17.33 | n/a | 10.00 | -3.26 | 0.229 | ✅ |
| COED-BSED-SS | COED | graduation_count | 0.52 | 0.66 | 1/3 | 0.19 | 0.57 | 0/3 MCMC | 0.67 | 0.40 | 0.33 | 1.33 | 0.33 | +0.19 | -0.978 | ⚠️ NO |
| COED-BSIE-IA | COED | enrollment_count | 2.86 | 2.91 | 0/3 | 2.10 | 0.05 | 0/3 MCMC | 7.00 | 17.89 | 16.00 | n/a | 7.00 | -4.14 | 0.852 | ✅ |
| COED-BSIE-IA | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSNED | COED | enrollment_count | 3.23 | 4.03 | 0/3 | 1.21 | 0.02 | 0/3 MCMC | 7.00 | 21.31 | 18.67 | n/a | 7.00 | -3.77 | 0.776 | ✅ |
| COED-BSNED | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BTLED-HE | COED | enrollment_count | 1.99 | 2.08 | 0/3 | 0.25 | 0.01 | 0/3 MCMC | 7.67 | 16.07 | 15.00 | n/a | 7.67 | -5.68 | 0.930 | ✅ |
| COED-BTLED-HE | COED | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-CERT-PTE | COED | enrollment_count | 9.98 | 11.99 | 0/3 | 2.84 | 0.14 | 0/3 MCMC | 3.33 | 3.78 | 7.33 | n/a | 3.33 | +6.65 | -7.515 | ⚠️ NO |
| COED-CERT-PTE | COED | graduation_count | 5.73 | 6.33 | 0/3 | 1.81 | 0.29 | 0/3 MCMC | 5.33 | 3.15 | 5.33 | 12.49 | 3.15 | +2.58 | -2.677 | ⚠️ NO |
| COED-PBD-ALS | COED | enrollment_count | 7.44 | 9.02 | 0/3 | 2.59 | 0.11 | 0/3 MCMC | 3.33 | 9.31 | 6.67 | n/a | 3.33 | +4.11 | -18.255 | ⚠️ NO |
| COED-PBD-ALS | COED | graduation_count | 2.35 | 2.65 | 0/3 | 0.62 | 0.11 | 0/3 MCMC | 3.67 | 3.46 | 2.33 | 3.94 | 2.33 | +0.01 | -3.531 | ⚠️ NO |
| CPADM-BPA | CPADM | enrollment_count | 11.35 | 12.56 | 0/3 | 1.60 | 0.01 | 0/3 MCMC | 33.33 | 81.09 | 74.67 | n/a | 33.33 | -21.98 | 0.892 | ✅ |
| CPADM-BPA | CPADM | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CPADM-BPA-DRM | CPADM | enrollment_count | 4.38 | 6.00 | 0/3 | 0.16 | 0.00 | 0/3 MCMC | 16.00 | 43.97 | 39.33 | n/a | 16.00 | -11.62 | 0.899 | ✅ |
| CPADM-BPA-DRM | CPADM | graduation_count | 0.00 | 0.00 | 3/3 | 0.00 | n/a | 0/3 MCMC | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| IPE-CERT-PE | IPE | enrollment_count | 23.09 | 25.22 | 0/3 | 5.16 | 0.04 | 0/3 MCMC | 47.00 | 24.84 | 23.00 | n/a | 23.00 | +0.09 | 0.041 | ⚠️ NO |
| IPE-CERT-PE | IPE | graduation_count | 13.29 | 14.24 | 0/3 | 6.52 | 0.17 | 0/3 MCMC | 10.33 | 16.81 | 8.00 | 49.10 | 8.00 | +5.29 | -4.333 | ⚠️ NO |
