# Forecast Model Evaluation Report

Prophet beats the best baseline on **43 of 74** series (58%) overall.

**Breakdown by metric:**

- `enrollment_count`: 27 of 37 (73%)
- `graduation_count`: 16 of 37 (43%)

**Interval calibration:** 0 of 222 Prophet walk-forward folds (0%) got a genuine MCMC-calibrated 80% interval; the rest fell back to a disclosed MAP-only approximation (training window below `MCMC_MIN_TRAIN_POINTS`, or MCMC sampling diverged -- see `fit_prophet`/`IntervalCalibration`). Coverage figures below apply to whichever interval each fold actually got.

| Program | College | Metric | Prophet MAE | Prophet RMSE | 80% Interval Coverage | Mean Interval Width | Normalized Width | Interval Calibration | Naive MAE | Hist. Avg MAE | Seasonal Naive MAE | Count Model MAE | Best Baseline MAE | Diff (Prophet - Baseline) | Prophet R² | Beats Baseline? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CICT-BSDS | CICT | enrollment_count | 7.28 | 7.86 | 0/3 | 4.61 | 0.02 | 0/3 MCMC | 36.00 | 132.87 | 67.67 | n/a | 36.00 | -28.72 | 0.952 | ✅ |
| CICT-BSDS | CICT | graduation_count | 8.74 | 9.97 | 0/3 | 0.88 | 0.06 | 0/3 MCMC | 8.33 | 14.31 | 12.33 | 5.94 | 8.33 | +0.41 | -1.249 | ⚠️ NO |
| CICT-BSIT-DB | CICT | enrollment_count | 37.94 | 40.61 | 0/3 | 10.93 | 0.03 | 0/3 MCMC | 44.00 | 121.95 | 30.33 | n/a | 30.33 | +7.61 | -1.955 | ⚠️ NO |
| CICT-BSIT-DB | CICT | graduation_count | 10.95 | 13.92 | 0/3 | 0.09 | 0.00 | 0/3 MCMC | 11.00 | 22.57 | 18.00 | 31.00 | 11.00 | -0.05 | -5.054 | ✅ |
| CICT-BSIT-NET | CICT | enrollment_count | 28.47 | 30.41 | 0/3 | 4.38 | 0.01 | 0/3 MCMC | 44.00 | 116.54 | 42.00 | n/a | 42.00 | -13.53 | -0.200 | ✅ |
| CICT-BSIT-NET | CICT | graduation_count | 17.24 | 18.59 | 0/3 | 1.23 | 0.04 | 0/3 MCMC | 19.00 | 24.66 | 18.67 | 42.00 | 18.67 | -1.43 | -3.236 | ✅ |
| CICT-BSIT-WEB | CICT | enrollment_count | 23.90 | 35.03 | 1/3 | 6.07 | 0.02 | 0/3 MCMC | 54.33 | 143.92 | 54.67 | n/a | 54.33 | -30.43 | -0.063 | ✅ |
| CICT-BSIT-WEB | CICT | graduation_count | 12.80 | 13.46 | 0/3 | 1.22 | 0.05 | 0/3 MCMC | 11.67 | 23.83 | 20.00 | 24.02 | 11.67 | +1.13 | -2.758 | ⚠️ NO |
| CMBT-BSBA-BPO | CMBT | enrollment_count | 6.22 | 6.73 | 0/3 | 5.62 | 0.03 | 0/3 MCMC | 17.00 | 85.06 | 38.67 | n/a | 17.00 | -10.78 | 0.832 | ✅ |
| CMBT-BSBA-BPO | CMBT | graduation_count | 6.25 | 6.67 | 0/3 | 1.14 | 0.13 | 0/3 MCMC | 7.00 | 7.71 | 6.00 | 7.07 | 6.00 | +0.25 | -1.062 | ⚠️ NO |
| CMBT-BSBA-ECON | CMBT | enrollment_count | 6.37 | 7.59 | 1/3 | 5.91 | 0.03 | 0/3 MCMC | 20.33 | 72.65 | 31.33 | n/a | 20.33 | -13.96 | 0.807 | ✅ |
| CMBT-BSBA-ECON | CMBT | graduation_count | 7.27 | 8.21 | 0/3 | 0.25 | 0.02 | 0/3 MCMC | 7.67 | 10.70 | 8.67 | 12.33 | 7.67 | -0.39 | -1.443 | ✅ |
| CMBT-BSBA-FM | CMBT | enrollment_count | 12.03 | 12.70 | 0/3 | 4.30 | 0.01 | 0/3 MCMC | 38.00 | 121.70 | 45.33 | n/a | 38.00 | -25.97 | 0.736 | ✅ |
| CMBT-BSBA-FM | CMBT | graduation_count | 12.44 | 13.55 | 0/3 | 1.07 | 0.06 | 0/3 MCMC | 14.00 | 17.31 | 12.67 | 34.00 | 12.67 | -0.23 | -4.583 | ✅ |
| CMBT-BSBA-HRM | CMBT | enrollment_count | 14.51 | 16.99 | 0/3 | 5.81 | 0.02 | 0/3 MCMC | 25.00 | 97.50 | 35.33 | n/a | 25.00 | -10.49 | 0.072 | ✅ |
| CMBT-BSBA-HRM | CMBT | graduation_count | 7.27 | 8.55 | 0/3 | 0.26 | 0.02 | 0/3 MCMC | 7.67 | 12.90 | 10.00 | 20.33 | 7.67 | -0.40 | -5.327 | ✅ |
| CMBT-BSBA-MM | CMBT | enrollment_count | 15.55 | 17.23 | 0/3 | 3.69 | 0.02 | 0/3 MCMC | 34.33 | 94.97 | 45.00 | n/a | 34.33 | -18.78 | 0.601 | ✅ |
| CMBT-BSBA-MM | CMBT | graduation_count | 5.80 | 5.93 | 0/3 | 1.09 | 0.10 | 0/3 MCMC | 5.00 | 9.92 | 8.67 | 8.88 | 5.00 | +0.80 | -2.301 | ⚠️ NO |
| CMBT-BSENTREP | CMBT | enrollment_count | 25.41 | 28.73 | 0/3 | 8.94 | 0.04 | 0/3 MCMC | 19.33 | 87.98 | 28.67 | n/a | 19.33 | +6.08 | -3.866 | ⚠️ NO |
| CMBT-BSENTREP | CMBT | graduation_count | 9.77 | 12.98 | 1/3 | 1.04 | 0.06 | 0/3 MCMC | 11.33 | 15.20 | 12.33 | 38.67 | 11.33 | -1.56 | -14.466 | ✅ |
| CMBT-BSHM | CMBT | enrollment_count | 15.25 | 17.41 | 0/3 | 3.90 | 0.02 | 0/3 MCMC | 25.00 | 81.39 | 28.67 | n/a | 25.00 | -9.75 | -0.189 | ✅ |
| CMBT-BSHM | CMBT | graduation_count | 11.97 | 12.91 | 0/3 | 1.62 | 0.10 | 0/3 MCMC | 13.67 | 14.71 | 10.00 | 31.67 | 10.00 | +1.97 | -4.394 | ⚠️ NO |
| CMBT-BSTM | CMBT | enrollment_count | 5.71 | 6.03 | 0/3 | 4.45 | 0.03 | 0/3 MCMC | 20.33 | 74.74 | 34.67 | n/a | 20.33 | -14.62 | 0.883 | ✅ |
| CMBT-BSTM | CMBT | graduation_count | 6.17 | 7.26 | 0/3 | 1.12 | 0.13 | 0/3 MCMC | 5.33 | 8.04 | 7.67 | 1.79 | 5.33 | +0.84 | -0.782 | ⚠️ NO |
| COA-BSARCH | COA | enrollment_count | 23.20 | 23.23 | 0/3 | 20.80 | 0.10 | 0/3 MCMC | 24.33 | 78.05 | 26.33 | n/a | 24.33 | -1.13 | -0.735 | ✅ |
| COA-BSARCH | COA | graduation_count | 3.89 | 6.20 | 1/3 | 1.49 | 0.28 | 0/3 MCMC | 4.33 | 4.51 | 4.33 | 4.65 | 4.33 | -0.45 | -0.301 | ✅ |
| COA-CERT-BUT | COA | enrollment_count | 8.16 | 9.47 | 0/3 | 6.47 | 0.19 | 0/3 MCMC | 7.33 | 15.14 | 11.67 | n/a | 7.33 | +0.83 | -0.858 | ⚠️ NO |
| COA-CERT-BUT | COA | graduation_count | 2.88 | 3.16 | 1/3 | 3.43 | 2.06 | 0/3 MCMC | 1.67 | 1.36 | 2.00 | 1.61 | 1.36 | +1.53 | -5.402 | ⚠️ NO |
| COA-CERT-CADD | COA | enrollment_count | 9.00 | 10.58 | 1/3 | 7.38 | 0.16 | 0/3 MCMC | 7.33 | 20.25 | 16.67 | n/a | 7.33 | +1.66 | -0.196 | ⚠️ NO |
| COA-CERT-CADD | COA | graduation_count | 3.65 | 4.56 | 1/3 | 3.51 | 0.42 | 0/3 MCMC | 5.00 | 4.35 | 3.67 | 3.78 | 3.67 | -0.02 | -0.183 | ✅ |
| COA-CERT-DRAFT | COA | enrollment_count | 14.71 | 16.01 | 0/3 | 8.26 | 0.15 | 0/3 MCMC | 7.67 | 11.17 | 9.67 | n/a | 7.67 | +7.04 | -3.786 | ⚠️ NO |
| COA-CERT-DRAFT | COA | graduation_count | 3.58 | 4.58 | 2/3 | 4.43 | 0.44 | 0/3 MCMC | 4.33 | 2.83 | 3.00 | 4.27 | 2.83 | +0.75 | -3.488 | ⚠️ NO |
| COC-BSCRIM | COC | enrollment_count | 34.86 | 37.46 | 0/3 | 29.18 | 0.07 | 0/3 MCMC | 68.67 | 161.51 | 61.00 | n/a | 61.00 | -26.14 | 0.208 | ✅ |
| COC-BSCRIM | COC | graduation_count | 16.84 | 19.82 | 0/3 | 1.34 | 0.04 | 0/3 MCMC | 18.67 | 28.88 | 22.00 | 42.57 | 18.67 | -1.82 | -6.157 | ✅ |
| COE-BSCE | COE | enrollment_count | 6.01 | 7.97 | 1/3 | 4.01 | 0.01 | 0/3 MCMC | 28.00 | 124.84 | 52.33 | n/a | 28.00 | -21.99 | 0.889 | ✅ |
| COE-BSCE | COE | graduation_count | 4.09 | 5.82 | 0/3 | 0.78 | 0.15 | 0/3 MCMC | 4.00 | 4.98 | 4.67 | 3.52 | 4.00 | +0.09 | -0.527 | ⚠️ NO |
| COE-BSEE | COE | enrollment_count | 55.86 | 79.83 | 0/3 | 4.14 | 0.02 | 0/3 MCMC | 36.67 | 124.04 | 58.33 | n/a | 36.67 | +19.19 | -5.182 | ⚠️ NO |
| COE-BSEE | COE | graduation_count | 3.21 | 4.47 | 0/3 | 0.13 | 0.03 | 0/3 MCMC | 3.00 | 3.85 | 3.67 | 2.33 | 3.00 | +0.21 | -0.579 | ⚠️ NO |
| COE-BSME | COE | enrollment_count | 17.76 | 20.90 | 0/3 | 4.78 | 0.02 | 0/3 MCMC | 21.33 | 86.52 | 41.67 | n/a | 21.33 | -3.58 | -0.119 | ✅ |
| COE-BSME | COE | graduation_count | 5.11 | 7.57 | 0/3 | 1.04 | 0.19 | 0/3 MCMC | 4.67 | 5.20 | 5.67 | 4.84 | 4.67 | +0.44 | -0.499 | ⚠️ NO |
| COED-BECED | COED | enrollment_count | 4.11 | 5.12 | 2/3 | 6.06 | 0.05 | 0/3 MCMC | 15.33 | 48.48 | 24.33 | n/a | 15.33 | -11.22 | 0.845 | ✅ |
| COED-BECED | COED | graduation_count | 4.78 | 5.45 | 0/3 | 0.59 | 0.07 | 0/3 MCMC | 4.33 | 8.14 | 6.67 | 5.00 | 4.33 | +0.44 | -1.346 | ⚠️ NO |
| COED-BEED | COED | enrollment_count | 6.04 | 7.13 | 1/3 | 7.26 | 0.04 | 0/3 MCMC | 30.67 | 79.52 | 39.00 | n/a | 30.67 | -24.63 | 0.896 | ✅ |
| COED-BEED | COED | graduation_count | 4.46 | 5.45 | 1/3 | 1.17 | 0.12 | 0/3 MCMC | 4.33 | 8.70 | 7.00 | 6.99 | 4.33 | +0.13 | -2.709 | ⚠️ NO |
| COED-BPE | COED | enrollment_count | 4.31 | 5.23 | 2/3 | 8.91 | 0.08 | 0/3 MCMC | 10.33 | 42.69 | 18.33 | n/a | 10.33 | -6.02 | 0.665 | ✅ |
| COED-BPE | COED | graduation_count | 4.41 | 4.91 | 0/3 | 0.25 | 0.03 | 0/3 MCMC | 4.00 | 6.80 | 6.00 | 4.00 | 4.00 | +0.41 | -1.083 | ⚠️ NO |
| COED-BSED-ENG | COED | enrollment_count | 5.86 | 6.71 | 1/3 | 7.01 | 0.04 | 0/3 MCMC | 19.00 | 60.85 | 28.67 | n/a | 19.00 | -13.14 | 0.769 | ✅ |
| COED-BSED-ENG | COED | graduation_count | 11.75 | 16.07 | 0/3 | 1.11 | 0.07 | 0/3 MCMC | 11.33 | 15.02 | 14.00 | 6.53 | 11.33 | +0.42 | -0.569 | ⚠️ NO |
| COED-BSED-FIL | COED | enrollment_count | 8.40 | 8.82 | 0/3 | 2.70 | 0.03 | 0/3 MCMC | 13.00 | 36.21 | 11.67 | n/a | 11.67 | -3.27 | -0.281 | ✅ |
| COED-BSED-FIL | COED | graduation_count | 6.54 | 6.78 | 0/3 | 1.07 | 0.11 | 0/3 MCMC | 7.33 | 8.11 | 5.33 | 15.00 | 5.33 | +1.21 | -3.219 | ⚠️ NO |
| COED-BSED-MATH | COED | enrollment_count | 9.19 | 10.39 | 0/3 | 3.41 | 0.03 | 0/3 MCMC | 25.00 | 53.71 | 24.00 | n/a | 24.00 | -14.81 | 0.634 | ✅ |
| COED-BSED-MATH | COED | graduation_count | 4.51 | 4.70 | 0/3 | 0.88 | 0.10 | 0/3 MCMC | 4.00 | 7.77 | 6.67 | 2.69 | 4.00 | +0.51 | -1.069 | ⚠️ NO |
| COED-BSED-PHYS | COED | enrollment_count | 5.16 | 6.79 | 1/3 | 0.84 | 0.01 | 0/3 MCMC | 10.33 | 25.12 | 10.33 | n/a | 10.33 | -5.17 | -0.059 | ✅ |
| COED-BSED-PHYS | COED | graduation_count | 2.13 | 2.81 | 1/3 | 1.22 | 0.17 | 0/3 MCMC | 2.67 | 5.89 | 4.33 | 5.39 | 2.67 | -0.53 | -10.829 | ✅ |
| COED-BSED-SCI | COED | enrollment_count | 5.78 | 6.49 | 0/3 | 1.50 | 0.01 | 0/3 MCMC | 15.33 | 44.11 | 19.67 | n/a | 15.33 | -9.56 | 0.686 | ✅ |
| COED-BSED-SCI | COED | graduation_count | 2.64 | 2.93 | 0/3 | 0.52 | 0.10 | 0/3 MCMC | 3.33 | 4.55 | 4.00 | 8.35 | 3.33 | -0.70 | -4.527 | ✅ |
| COED-BSED-SS | COED | enrollment_count | 2.99 | 3.48 | 1/3 | 5.28 | 0.06 | 0/3 MCMC | 12.33 | 41.41 | 17.00 | n/a | 12.33 | -9.34 | 0.872 | ✅ |
| COED-BSED-SS | COED | graduation_count | 6.33 | 7.14 | 0/3 | 0.99 | 0.14 | 0/3 MCMC | 7.67 | 6.44 | 4.67 | 19.33 | 4.67 | +1.66 | -4.335 | ⚠️ NO |
| COED-BSIE-IA | COED | enrollment_count | 6.09 | 8.30 | 1/3 | 4.74 | 0.05 | 0/3 MCMC | 11.33 | 43.34 | 23.00 | n/a | 11.33 | -5.24 | 0.430 | ✅ |
| COED-BSIE-IA | COED | graduation_count | 4.76 | 4.94 | 0/3 | 0.41 | 0.07 | 0/3 MCMC | 5.33 | 5.20 | 4.00 | 6.08 | 4.00 | +0.76 | -1.242 | ⚠️ NO |
| COED-BSNED | COED | enrollment_count | 7.94 | 8.03 | 0/3 | 2.50 | 0.03 | 0/3 MCMC | 8.33 | 34.99 | 15.67 | n/a | 8.33 | -0.39 | -1.285 | ✅ |
| COED-BSNED | COED | graduation_count | 2.46 | 3.52 | 0/3 | 0.13 | 0.02 | 0/3 MCMC | 2.67 | 5.60 | 4.33 | 10.00 | 2.67 | -0.20 | -54.724 | ✅ |
| COED-BTLED-HE | COED | enrollment_count | 5.39 | 6.07 | 0/3 | 1.44 | 0.02 | 0/3 MCMC | 9.33 | 31.66 | 11.00 | n/a | 9.33 | -3.94 | 0.137 | ✅ |
| COED-BTLED-HE | COED | graduation_count | 3.32 | 4.30 | 0/3 | 0.66 | 0.11 | 0/3 MCMC | 3.33 | 5.24 | 4.33 | 1.84 | 3.33 | -0.01 | -0.459 | ✅ |
| COED-CERT-PTE | COED | enrollment_count | 4.78 | 5.43 | 0/3 | 2.59 | 0.06 | 0/3 MCMC | 4.33 | 16.91 | 11.33 | n/a | 4.33 | +0.44 | -1.711 | ⚠️ NO |
| COED-CERT-PTE | COED | graduation_count | 2.99 | 4.07 | 1/3 | 2.20 | 0.30 | 0/3 MCMC | 7.33 | 5.06 | 3.33 | 5.47 | 3.33 | -0.34 | 0.182 | ✅ |
| COED-PBD-ALS | COED | enrollment_count | 9.52 | 11.01 | 0/3 | 9.14 | 0.26 | 0/3 MCMC | 4.33 | 10.18 | 5.00 | n/a | 4.33 | +5.18 | -33.067 | ⚠️ NO |
| COED-PBD-ALS | COED | graduation_count | 4.68 | 5.48 | 0/3 | 2.42 | 0.48 | 0/3 MCMC | 3.67 | 2.73 | 4.67 | 6.78 | 2.73 | +1.95 | -2.463 | ⚠️ NO |
| CPADM-BPA | CPADM | enrollment_count | 18.26 | 20.24 | 0/3 | 6.61 | 0.02 | 0/3 MCMC | 36.00 | 120.37 | 45.00 | n/a | 36.00 | -17.74 | 0.361 | ✅ |
| CPADM-BPA | CPADM | graduation_count | 11.97 | 12.19 | 0/3 | 1.47 | 0.07 | 0/3 MCMC | 10.00 | 20.04 | 17.33 | 13.33 | 10.00 | +1.97 | -1.750 | ⚠️ NO |
| CPADM-BPA-DRM | CPADM | enrollment_count | 25.79 | 28.34 | 0/3 | 4.38 | 0.02 | 0/3 MCMC | 21.33 | 74.73 | 20.33 | n/a | 20.33 | +5.45 | -5.467 | ⚠️ NO |
| CPADM-BPA-DRM | CPADM | graduation_count | 8.18 | 9.47 | 0/3 | 1.11 | 0.07 | 0/3 MCMC | 9.00 | 13.29 | 10.00 | 19.76 | 9.00 | -0.82 | -7.407 | ✅ |
| IPE-CERT-PE | IPE | enrollment_count | 16.76 | 17.29 | 0/3 | 21.26 | 0.15 | 0/3 MCMC | 45.67 | 21.96 | 10.67 | n/a | 10.67 | +6.09 | 0.507 | ⚠️ NO |
| IPE-CERT-PE | IPE | graduation_count | 5.86 | 6.66 | 1/3 | 8.58 | 0.21 | 0/3 MCMC | 14.00 | 16.69 | 9.67 | 14.65 | 9.67 | -3.81 | 0.486 | ✅ |
