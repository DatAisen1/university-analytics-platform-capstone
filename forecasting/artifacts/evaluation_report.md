# Forecast Model Evaluation Report

Prophet beats the best baseline on **31 of 74** series (42%) overall.

**Breakdown by metric:**

- `enrollment_count`: 31 of 37 (84%)
- `graduation_count`: 0 of 37 (0%)

| Program | College | Metric | Prophet MAE | Naive MAE | Hist. Avg MAE | Seasonal Naive MAE | Count Model MAE | Best Baseline MAE | Diff (Prophet - Baseline) | Prophet R² | Beats Baseline? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CICT-BSDS | CICT | enrollment_count | 3.29 | 28.00 | 67.75 | 61.33 | n/a | 28.00 | -24.71 | 0.984 | ✅ |
| CICT-BSDS | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-DB | CICT | enrollment_count | 7.55 | 40.33 | 94.27 | 88.00 | n/a | 40.33 | -32.78 | 0.967 | ✅ |
| CICT-BSIT-DB | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-NET | CICT | enrollment_count | 7.16 | 33.00 | 79.87 | 71.67 | n/a | 33.00 | -25.84 | 0.954 | ✅ |
| CICT-BSIT-NET | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-WEB | CICT | enrollment_count | 8.46 | 43.67 | 108.64 | 98.67 | n/a | 43.67 | -35.21 | 0.969 | ✅ |
| CICT-BSIT-WEB | CICT | graduation_count | 0.52 | 0.67 | 0.40 | 0.33 | 847298.55 | 0.33 | +0.19 | -0.978 | ⚠️ NO |
| CMBT-BSBA-BPO | CMBT | enrollment_count | 14.33 | 16.00 | 49.23 | 42.00 | n/a | 16.00 | -1.67 | 0.299 | ✅ |
| CMBT-BSBA-BPO | CMBT | graduation_count | 0.33 | 0.33 | 0.33 | 0.33 | 0.33 | 0.33 | +0.00 | -0.500 | ⚠️ NO |
| CMBT-BSBA-ECON | CMBT | enrollment_count | 6.16 | 19.00 | 41.03 | 36.33 | n/a | 19.00 | -12.84 | 0.856 | ✅ |
| CMBT-BSBA-ECON | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-FM | CMBT | enrollment_count | 11.25 | 34.67 | 87.84 | 79.67 | n/a | 34.67 | -23.41 | 0.881 | ✅ |
| CMBT-BSBA-FM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-HRM | CMBT | enrollment_count | 10.30 | 30.00 | 81.14 | 74.33 | n/a | 30.00 | -19.70 | 0.878 | ✅ |
| CMBT-BSBA-HRM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-MM | CMBT | enrollment_count | 2.11 | 27.00 | 56.39 | 51.67 | n/a | 27.00 | -24.89 | 0.989 | ✅ |
| CMBT-BSBA-MM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSENTREP | CMBT | enrollment_count | 11.23 | 23.00 | 60.33 | 52.67 | n/a | 23.00 | -11.77 | 0.733 | ✅ |
| CMBT-BSENTREP | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSHM | CMBT | enrollment_count | 5.44 | 23.00 | 64.37 | 58.33 | n/a | 23.00 | -17.56 | 0.952 | ✅ |
| CMBT-BSHM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CMBT-BSTM | CMBT | enrollment_count | 3.63 | 14.67 | 38.48 | 35.33 | n/a | 14.67 | -11.04 | 0.953 | ✅ |
| CMBT-BSTM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COA-BSARCH | COA | enrollment_count | 10.46 | 30.33 | 67.70 | 62.00 | n/a | 30.33 | -19.87 | 0.821 | ✅ |
| COA-BSARCH | COA | graduation_count | 0.63 | 1.00 | 0.68 | 0.33 | 23992341.35 | 0.33 | +0.29 | -1.324 | ⚠️ NO |
| COA-CERT-BUT | COA | enrollment_count | 2.91 | 4.33 | 2.23 | 1.00 | n/a | 1.00 | +1.91 | -1.644 | ⚠️ NO |
| COA-CERT-BUT | COA | graduation_count | 3.56 | 3.33 | 1.72 | 0.67 | 7.30 | 0.67 | +2.90 | -5.189 | ⚠️ NO |
| COA-CERT-CADD | COA | enrollment_count | 6.97 | 3.33 | 7.29 | 5.33 | n/a | 3.33 | +3.64 | -13.648 | ⚠️ NO |
| COA-CERT-CADD | COA | graduation_count | 3.50 | 2.67 | 3.30 | 3.00 | 7.08 | 2.67 | +0.84 | -0.382 | ⚠️ NO |
| COA-CERT-DRAFT | COA | enrollment_count | 14.14 | 9.00 | 9.12 | 6.67 | n/a | 6.67 | +7.47 | -12.147 | ⚠️ NO |
| COA-CERT-DRAFT | COA | graduation_count | 6.07 | 2.67 | 4.07 | 5.00 | 23.85 | 2.67 | +3.41 | -11.606 | ⚠️ NO |
| COC-BSCRIM | COC | enrollment_count | 16.06 | 64.33 | 151.51 | 138.00 | n/a | 64.33 | -48.28 | 0.869 | ✅ |
| COC-BSCRIM | COC | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COE-BSCE | COE | enrollment_count | 17.82 | 30.33 | 73.31 | 68.67 | n/a | 30.33 | -12.51 | 0.624 | ✅ |
| COE-BSCE | COE | graduation_count | 0.96 | 0.33 | 0.52 | 1.00 | 204425790.05 | 0.33 | +0.63 | -6.007 | ⚠️ NO |
| COE-BSEE | COE | enrollment_count | 5.73 | 28.00 | 65.70 | 60.00 | n/a | 28.00 | -22.27 | 0.940 | ✅ |
| COE-BSEE | COE | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COE-BSME | COE | enrollment_count | 13.71 | 17.00 | 48.13 | 43.33 | n/a | 17.00 | -3.29 | 0.298 | ✅ |
| COE-BSME | COE | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BECED | COED | enrollment_count | 2.82 | 10.67 | 29.23 | 25.67 | n/a | 10.67 | -7.85 | 0.941 | ✅ |
| COED-BECED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BEED | COED | enrollment_count | 5.79 | 20.00 | 49.48 | 42.67 | n/a | 20.00 | -14.21 | 0.886 | ✅ |
| COED-BEED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BPE | COED | enrollment_count | 10.81 | 14.33 | 31.46 | 30.67 | n/a | 14.33 | -3.53 | 0.531 | ✅ |
| COED-BPE | COED | graduation_count | 1.12 | 0.67 | 0.46 | 0.67 | 204425789.41 | 0.46 | +0.66 | -4.681 | ⚠️ NO |
| COED-BSED-ENG | COED | enrollment_count | 17.45 | 17.67 | 37.39 | 32.00 | n/a | 17.67 | -0.21 | -0.429 | ✅ |
| COED-BSED-ENG | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-FIL | COED | enrollment_count | 2.68 | 11.33 | 25.72 | 24.00 | n/a | 11.33 | -8.65 | 0.954 | ✅ |
| COED-BSED-FIL | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-MATH | COED | enrollment_count | 6.94 | 12.00 | 27.22 | 25.67 | n/a | 12.00 | -5.06 | 0.496 | ✅ |
| COED-BSED-MATH | COED | graduation_count | 0.48 | 0.33 | 0.60 | 0.67 | 847298.22 | 0.33 | +0.15 | -0.785 | ⚠️ NO |
| COED-BSED-PHYS | COED | enrollment_count | 2.20 | 3.67 | 11.48 | 9.67 | n/a | 3.67 | -1.47 | 0.588 | ✅ |
| COED-BSED-PHYS | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-SCI | COED | enrollment_count | 2.73 | 9.33 | 23.47 | 21.33 | n/a | 9.33 | -6.60 | 0.925 | ✅ |
| COED-BSED-SCI | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSED-SS | COED | enrollment_count | 6.74 | 10.00 | 19.97 | 17.33 | n/a | 10.00 | -3.26 | 0.229 | ✅ |
| COED-BSED-SS | COED | graduation_count | 0.52 | 0.67 | 0.40 | 0.33 | 847298.55 | 0.33 | +0.19 | -0.978 | ⚠️ NO |
| COED-BSIE-IA | COED | enrollment_count | 2.86 | 7.00 | 17.89 | 16.00 | n/a | 7.00 | -4.14 | 0.852 | ✅ |
| COED-BSIE-IA | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BSNED | COED | enrollment_count | 3.23 | 7.00 | 21.31 | 18.67 | n/a | 7.00 | -3.77 | 0.776 | ✅ |
| COED-BSNED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-BTLED-HE | COED | enrollment_count | 1.99 | 7.67 | 16.07 | 15.00 | n/a | 7.67 | -5.68 | 0.930 | ✅ |
| COED-BTLED-HE | COED | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| COED-CERT-PTE | COED | enrollment_count | 9.98 | 3.33 | 3.78 | 7.33 | n/a | 3.33 | +6.65 | -7.515 | ⚠️ NO |
| COED-CERT-PTE | COED | graduation_count | 5.73 | 5.33 | 3.15 | 5.33 | 12.49 | 3.15 | +2.58 | -2.677 | ⚠️ NO |
| COED-PBD-ALS | COED | enrollment_count | 7.44 | 3.33 | 9.31 | 6.67 | n/a | 3.33 | +4.11 | -18.255 | ⚠️ NO |
| COED-PBD-ALS | COED | graduation_count | 2.35 | 3.67 | 3.46 | 2.33 | 3.94 | 2.33 | +0.01 | -3.531 | ⚠️ NO |
| CPADM-BPA | CPADM | enrollment_count | 11.35 | 33.33 | 81.09 | 74.67 | n/a | 33.33 | -21.98 | 0.892 | ✅ |
| CPADM-BPA | CPADM | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| CPADM-BPA-DRM | CPADM | enrollment_count | 4.38 | 16.00 | 43.97 | 39.33 | n/a | 16.00 | -11.62 | 0.899 | ✅ |
| CPADM-BPA-DRM | CPADM | graduation_count | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 | 1.000 | ⚠️ NO |
| IPE-CERT-PE | IPE | enrollment_count | 23.09 | 47.00 | 24.84 | 23.00 | n/a | 23.00 | +0.09 | 0.041 | ⚠️ NO |
| IPE-CERT-PE | IPE | graduation_count | 13.29 | 10.33 | 16.81 | 8.00 | 69.95 | 8.00 | +5.29 | -4.333 | ⚠️ NO |
