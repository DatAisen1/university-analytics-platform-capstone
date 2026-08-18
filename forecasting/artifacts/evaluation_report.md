# Forecast Model Evaluation Report

Prophet beats the best baseline on **29 of 74** series (39%).

| Program | College | Metric | Prophet MAE | Naive MAE | Hist. Avg MAE | Seasonal Naive MAE | Best Baseline MAE | Diff (Prophet - Baseline) | Prophet R² | Beats Baseline? |
|---|---|---|---|---|---|---|---|---|---|---|
| CICT-BSDS | CICT | enrollment_count | 25.11 | 28.00 | 67.75 | 61.33 | 28.00 | -2.89 | 0.230 | ✅ |
| CICT-BSDS | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-DB | CICT | enrollment_count | 38.44 | 40.33 | 94.27 | 88.00 | 40.33 | -1.89 | 0.177 | ✅ |
| CICT-BSIT-DB | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-NET | CICT | enrollment_count | 26.58 | 33.00 | 79.87 | 71.67 | 33.00 | -6.42 | 0.255 | ✅ |
| CICT-BSIT-NET | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-WEB | CICT | enrollment_count | 34.54 | 43.67 | 108.64 | 98.67 | 43.67 | -9.12 | 0.283 | ✅ |
| CICT-BSIT-WEB | CICT | graduation_count | 0.35 | 0.67 | 0.40 | 0.33 | 0.33 | +0.01 | -0.502 | ⚠️ NO |
| CMBT-BSBA-BPO | CMBT | enrollment_count | 17.30 | 16.00 | 49.23 | 42.00 | 16.00 | +1.30 | 0.016 | ⚠️ NO |
| CMBT-BSBA-BPO | CMBT | graduation_count | 0.33 | 0.33 | 0.33 | 0.33 | 0.33 | +0.00 | -0.500 | ⚠️ NO |
| CMBT-BSBA-ECON | CMBT | enrollment_count | 15.66 | 19.00 | 41.03 | 36.33 | 19.00 | -3.34 | 0.089 | ✅ |
| CMBT-BSBA-ECON | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-FM | CMBT | enrollment_count | 31.81 | 34.67 | 87.84 | 79.67 | 34.67 | -2.85 | 0.258 | ✅ |
| CMBT-BSBA-FM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-HRM | CMBT | enrollment_count | 24.89 | 30.00 | 81.14 | 74.33 | 30.00 | -5.11 | 0.316 | ✅ |
| CMBT-BSBA-HRM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-MM | CMBT | enrollment_count | 21.64 | 27.00 | 56.39 | 51.67 | 27.00 | -5.36 | 0.153 | ✅ |
| CMBT-BSBA-MM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSENTREP | CMBT | enrollment_count | 21.06 | 23.00 | 60.33 | 52.67 | 23.00 | -1.94 | 0.173 | ✅ |
| CMBT-BSENTREP | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSHM | CMBT | enrollment_count | 18.67 | 23.00 | 64.37 | 58.33 | 23.00 | -4.33 | 0.354 | ✅ |
| CMBT-BSHM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSTM | CMBT | enrollment_count | 13.92 | 14.67 | 38.48 | 35.33 | 14.67 | -0.75 | 0.254 | ✅ |
| CMBT-BSTM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COA-BSARCH | COA | enrollment_count | 27.01 | 30.33 | 67.70 | 62.00 | 30.33 | -3.32 | 0.158 | ✅ |
| COA-BSARCH | COA | graduation_count | 1.00 | 1.00 | 0.68 | 0.33 | 0.33 | +0.67 | -3.493 | ⚠️ NO |
| COA-CERT-BUT | COA | enrollment_count | 3.52 | 4.33 | 2.23 | 1.00 | 1.00 | +2.52 | -1.964 | ⚠️ NO |
| COA-CERT-BUT | COA | graduation_count | 2.12 | 3.33 | 1.72 | 0.67 | 0.67 | +1.45 | -2.714 | ⚠️ NO |
| COA-CERT-CADD | COA | enrollment_count | 5.88 | 3.33 | 7.29 | 5.33 | 3.33 | +2.55 | -8.927 | ⚠️ NO |
| COA-CERT-CADD | COA | graduation_count | 3.02 | 2.67 | 3.30 | 3.00 | 2.67 | +0.35 | -0.030 | ⚠️ NO |
| COA-CERT-DRAFT | COA | enrollment_count | 12.78 | 9.00 | 9.12 | 6.67 | 6.67 | +6.11 | -9.224 | ⚠️ NO |
| COA-CERT-DRAFT | COA | graduation_count | 7.12 | 2.67 | 4.07 | 5.00 | 2.67 | +4.45 | -17.548 | ⚠️ NO |
| COC-BSCRIM | COC | enrollment_count | 48.71 | 64.33 | 151.51 | 138.00 | 64.33 | -15.62 | 0.255 | ✅ |
| COC-BSCRIM | COC | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COE-BSCE | COE | enrollment_count | 23.82 | 30.33 | 73.31 | 68.67 | 30.33 | -6.51 | 0.211 | ✅ |
| COE-BSCE | COE | graduation_count | 1.19 | 0.33 | 0.52 | 1.00 | 0.33 | +0.86 | -7.121 | ⚠️ NO |
| COE-BSEE | COE | enrollment_count | 23.42 | 28.00 | 65.70 | 60.00 | 28.00 | -4.58 | 0.229 | ✅ |
| COE-BSEE | COE | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COE-BSME | COE | enrollment_count | 13.97 | 17.00 | 48.13 | 43.33 | 17.00 | -3.03 | 0.366 | ✅ |
| COE-BSME | COE | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BECED | COED | enrollment_count | 8.88 | 10.67 | 29.23 | 25.67 | 10.67 | -1.79 | 0.342 | ✅ |
| COED-BECED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BEED | COED | enrollment_count | 15.67 | 20.00 | 49.48 | 42.67 | 20.00 | -4.33 | 0.220 | ✅ |
| COED-BEED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BPE | COED | enrollment_count | 16.46 | 14.33 | 31.46 | 30.67 | 14.33 | +2.12 | -0.151 | ⚠️ NO |
| COED-BPE | COED | graduation_count | 0.94 | 0.67 | 0.46 | 0.67 | 0.46 | +0.48 | -3.527 | ⚠️ NO |
| COED-BSED-ENG | COED | enrollment_count | 14.93 | 17.67 | 37.39 | 32.00 | 17.67 | -2.74 | -0.197 | ✅ |
| COED-BSED-ENG | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-FIL | COED | enrollment_count | 9.42 | 11.33 | 25.72 | 24.00 | 11.33 | -1.91 | 0.203 | ✅ |
| COED-BSED-FIL | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-MATH | COED | enrollment_count | 8.36 | 12.00 | 27.22 | 25.67 | 12.00 | -3.64 | 0.145 | ✅ |
| COED-BSED-MATH | COED | graduation_count | 0.65 | 0.33 | 0.60 | 0.67 | 0.33 | +0.32 | -1.885 | ⚠️ NO |
| COED-BSED-PHYS | COED | enrollment_count | 3.18 | 3.67 | 11.48 | 9.67 | 3.67 | -0.49 | 0.341 | ✅ |
| COED-BSED-PHYS | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-SCI | COED | enrollment_count | 8.80 | 9.33 | 23.47 | 21.33 | 9.33 | -0.53 | 0.246 | ✅ |
| COED-BSED-SCI | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-SS | COED | enrollment_count | 8.87 | 10.00 | 19.97 | 17.33 | 10.00 | -1.13 | -0.183 | ✅ |
| COED-BSED-SS | COED | graduation_count | 0.35 | 0.67 | 0.40 | 0.33 | 0.33 | +0.01 | -0.502 | ⚠️ NO |
| COED-BSIE-IA | COED | enrollment_count | 5.72 | 7.00 | 17.89 | 16.00 | 7.00 | -1.28 | 0.304 | ✅ |
| COED-BSIE-IA | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSNED | COED | enrollment_count | 4.96 | 7.00 | 21.31 | 18.67 | 7.00 | -2.04 | 0.492 | ✅ |
| COED-BSNED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BTLED-HE | COED | enrollment_count | 5.71 | 7.67 | 16.07 | 15.00 | 7.67 | -1.95 | 0.165 | ✅ |
| COED-BTLED-HE | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-CERT-PTE | COED | enrollment_count | 9.52 | 3.33 | 3.78 | 7.33 | 3.33 | +6.18 | -6.702 | ⚠️ NO |
| COED-CERT-PTE | COED | graduation_count | 9.59 | 5.33 | 3.15 | 5.33 | 3.15 | +6.44 | -8.246 | ⚠️ NO |
| COED-PBD-ALS | COED | enrollment_count | 8.01 | 3.33 | 9.31 | 6.67 | 3.33 | +4.67 | -17.595 | ⚠️ NO |
| COED-PBD-ALS | COED | graduation_count | 2.55 | 3.67 | 3.46 | 2.33 | 2.33 | +0.21 | -4.729 | ⚠️ NO |
| CPADM-BPA | CPADM | enrollment_count | 26.30 | 33.33 | 81.09 | 74.67 | 33.33 | -7.03 | 0.238 | ✅ |
| CPADM-BPA | CPADM | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CPADM-BPA-DRM | CPADM | enrollment_count | 12.67 | 16.00 | 43.97 | 39.33 | 16.00 | -3.33 | 0.355 | ✅ |
| CPADM-BPA-DRM | CPADM | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| IPE-CERT-PE | IPE | enrollment_count | 30.50 | 47.00 | 24.84 | 23.00 | 23.00 | +7.50 | -1.109 | ⚠️ NO |
| IPE-CERT-PE | IPE | graduation_count | 10.06 | 10.33 | 16.81 | 8.00 | 8.00 | +2.06 | -3.133 | ⚠️ NO |
