# Forecast Model Evaluation Report

Prophet beats the best baseline on **33 of 74** series (45%).

| Program | College | Metric | Prophet MAE | Naive MAE | Hist. Avg MAE | Prophet R² | Beats Baseline? |
|---|---|---|---|---|---|---|---|
| CICT-BSDS | CICT | enrollment_count | 25.17 | 28.00 | 67.75 | 0.229 | ✅ |
| CICT-BSDS | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-DB | CICT | enrollment_count | 38.34 | 40.33 | 94.27 | 0.178 | ✅ |
| CICT-BSIT-DB | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-NET | CICT | enrollment_count | 25.98 | 33.00 | 79.87 | 0.258 | ✅ |
| CICT-BSIT-NET | CICT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CICT-BSIT-WEB | CICT | enrollment_count | 33.97 | 43.67 | 108.64 | 0.286 | ✅ |
| CICT-BSIT-WEB | CICT | graduation_count | 0.35 | 0.67 | 0.40 | -0.502 | ✅ |
| CMBT-BSBA-BPO | CMBT | enrollment_count | 17.30 | 16.00 | 49.23 | 0.016 | ⚠️ NO |
| CMBT-BSBA-BPO | CMBT | graduation_count | 0.33 | 0.33 | 0.33 | -0.500 | ⚠️ NO |
| CMBT-BSBA-ECON | CMBT | enrollment_count | 15.80 | 19.00 | 41.03 | 0.086 | ✅ |
| CMBT-BSBA-ECON | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-FM | CMBT | enrollment_count | 32.18 | 34.67 | 87.84 | 0.253 | ✅ |
| CMBT-BSBA-FM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-HRM | CMBT | enrollment_count | 25.51 | 30.00 | 81.14 | 0.313 | ✅ |
| CMBT-BSBA-HRM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CMBT-BSBA-MM | CMBT | enrollment_count | 21.36 | 27.00 | 56.39 | 0.155 | ✅ |
| CMBT-BSBA-MM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CMBT-BSENTREP | CMBT | enrollment_count | 20.13 | 23.00 | 60.33 | 0.229 | ✅ |
| CMBT-BSENTREP | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CMBT-BSHM | CMBT | enrollment_count | 19.20 | 23.00 | 64.37 | 0.347 | ✅ |
| CMBT-BSHM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CMBT-BSTM | CMBT | enrollment_count | 13.64 | 14.67 | 38.48 | 0.262 | ✅ |
| CMBT-BSTM | CMBT | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COA-BSARCH | COA | enrollment_count | 25.21 | 30.33 | 67.70 | 0.186 | ✅ |
| COA-BSARCH | COA | graduation_count | 1.00 | 1.00 | 0.68 | -3.493 | ⚠️ NO |
| COA-CERT-BUT | COA | enrollment_count | 3.52 | 4.33 | 2.23 | -1.964 | ⚠️ NO |
| COA-CERT-BUT | COA | graduation_count | 2.12 | 3.33 | 1.72 | -2.714 | ⚠️ NO |
| COA-CERT-CADD | COA | enrollment_count | 5.88 | 3.33 | 7.29 | -8.926 | ⚠️ NO |
| COA-CERT-CADD | COA | graduation_count | 3.02 | 2.67 | 3.30 | -0.030 | ⚠️ NO |
| COA-CERT-DRAFT | COA | enrollment_count | 12.78 | 9.00 | 9.12 | -9.224 | ⚠️ NO |
| COA-CERT-DRAFT | COA | graduation_count | 7.12 | 2.67 | 4.07 | -17.548 | ⚠️ NO |
| COC-BSCRIM | COC | enrollment_count | 48.73 | 64.33 | 151.51 | 0.255 | ✅ |
| COC-BSCRIM | COC | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COE-BSCE | COE | enrollment_count | 25.85 | 30.33 | 73.31 | 0.194 | ✅ |
| COE-BSCE | COE | graduation_count | 1.19 | 0.33 | 0.52 | -7.121 | ⚠️ NO |
| COE-BSEE | COE | enrollment_count | 23.33 | 28.00 | 65.70 | 0.230 | ✅ |
| COE-BSEE | COE | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COE-BSME | COE | enrollment_count | 13.49 | 17.00 | 48.13 | 0.373 | ✅ |
| COE-BSME | COE | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BECED | COED | enrollment_count | 8.93 | 10.67 | 29.23 | 0.339 | ✅ |
| COED-BECED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BEED | COED | enrollment_count | 15.47 | 20.00 | 49.48 | 0.224 | ✅ |
| COED-BEED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BPE | COED | enrollment_count | 16.46 | 14.33 | 31.46 | -0.151 | ⚠️ NO |
| COED-BPE | COED | graduation_count | 0.94 | 0.67 | 0.46 | -3.527 | ⚠️ NO |
| COED-BSED-ENG | COED | enrollment_count | 14.93 | 17.67 | 37.39 | -0.197 | ✅ |
| COED-BSED-ENG | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BSED-FIL | COED | enrollment_count | 9.31 | 11.33 | 25.72 | 0.206 | ✅ |
| COED-BSED-FIL | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BSED-MATH | COED | enrollment_count | 8.36 | 12.00 | 27.22 | 0.145 | ✅ |
| COED-BSED-MATH | COED | graduation_count | 0.65 | 0.33 | 0.60 | -1.885 | ⚠️ NO |
| COED-BSED-PHYS | COED | enrollment_count | 2.78 | 3.67 | 11.48 | 0.373 | ✅ |
| COED-BSED-PHYS | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BSED-SCI | COED | enrollment_count | 8.63 | 9.33 | 23.47 | 0.256 | ✅ |
| COED-BSED-SCI | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BSED-SS | COED | enrollment_count | 9.28 | 10.00 | 19.97 | -0.309 | ✅ |
| COED-BSED-SS | COED | graduation_count | 0.35 | 0.67 | 0.40 | -0.502 | ✅ |
| COED-BSIE-IA | COED | enrollment_count | 5.68 | 7.00 | 17.89 | 0.307 | ✅ |
| COED-BSIE-IA | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BSNED | COED | enrollment_count | 5.22 | 7.00 | 21.31 | 0.487 | ✅ |
| COED-BSNED | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-BTLED-HE | COED | enrollment_count | 5.70 | 7.67 | 16.07 | 0.165 | ✅ |
| COED-BTLED-HE | COED | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COED-CERT-PTE | COED | enrollment_count | 9.52 | 3.33 | 3.78 | -6.702 | ⚠️ NO |
| COED-CERT-PTE | COED | graduation_count | 9.69 | 5.33 | 3.15 | -8.505 | ⚠️ NO |
| COED-PBD-ALS | COED | enrollment_count | 8.01 | 3.33 | 9.31 | -17.595 | ⚠️ NO |
| COED-PBD-ALS | COED | graduation_count | 2.55 | 3.67 | 3.46 | -4.729 | ✅ |
| CPADM-BPA | CPADM | enrollment_count | 26.34 | 33.33 | 81.09 | 0.238 | ✅ |
| CPADM-BPA | CPADM | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| CPADM-BPA-DRM | CPADM | enrollment_count | 12.62 | 16.00 | 43.97 | 0.355 | ✅ |
| CPADM-BPA-DRM | CPADM | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| IPE-CERT-PE | IPE | enrollment_count | 30.50 | 47.00 | 24.84 | -1.109 | ⚠️ NO |
| IPE-CERT-PE | IPE | graduation_count | 10.06 | 10.33 | 16.81 | -3.133 | ✅ |
