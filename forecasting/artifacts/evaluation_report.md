# Forecast Model Evaluation Report

Prophet beats the best baseline on **8 of 16** series (50%).

| College | Metric | Prophet MAE | Naive MAE | Hist. Avg MAE | Prophet R² | Beats Baseline? |
|---|---|---|---|---|---|---|
| CICT | enrollment_count | 24.99 | 222.00 | 569.12 | 0.980 | ✅ |
| CICT | graduation_count | 24.75 | 24.75 | 24.75 | -0.333 | ⚠️ NO |
| CMBT | enrollment_count | 46.95 | 259.75 | 673.70 | 0.892 | ✅ |
| CMBT | graduation_count | 28.30 | 28.50 | 28.04 | -0.323 | ⚠️ NO |
| COA | enrollment_count | 23.45 | 50.75 | 62.68 | 0.071 | ✅ |
| COA | graduation_count | 8.05 | 6.75 | 11.58 | -0.537 | ⚠️ NO |
| COC | enrollment_count | 26.44 | 86.00 | 215.95 | 0.745 | ✅ |
| COC | graduation_count | 7.56 | 7.50 | 7.37 | -0.305 | ⚠️ NO |
| COE | enrollment_count | 29.63 | 103.50 | 258.64 | 0.797 | ✅ |
| COE | graduation_count | 0.84 | 0.25 | 0.38 | 0.000 | ⚠️ NO |
| COED | enrollment_count | 40.95 | 198.00 | 449.72 | 0.863 | ✅ |
| COED | graduation_count | 25.00 | 21.50 | 26.23 | -0.082 | ⚠️ NO |
| CPADM | enrollment_count | 12.67 | 71.25 | 179.12 | 0.943 | ✅ |
| CPADM | graduation_count | 5.69 | 5.25 | 5.37 | -0.357 | ⚠️ NO |
| IPE | enrollment_count | 28.20 | 52.00 | 38.36 | -1.006 | ✅ |
| IPE | graduation_count | 7.36 | 5.50 | 20.03 | -1.156 | ⚠️ NO |
