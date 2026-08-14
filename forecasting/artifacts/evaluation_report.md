# Forecast Model Evaluation Report

Prophet beats the best baseline on **9 of 16** series (56%).

| College | Metric | Prophet MAE | Naive MAE | Hist. Avg MAE | Prophet R² | Beats Baseline? |
|---|---|---|---|---|---|---|
| CICT | enrollment_count | 114.37 | 145.00 | 350.53 | 0.265 | ✅ |
| CICT | graduation_count | 0.35 | 0.67 | 0.40 | -0.502 | ✅ |
| CMBT | enrollment_count | 146.32 | 187.33 | 478.81 | 0.317 | ✅ |
| CMBT | graduation_count | 0.33 | 0.33 | 0.33 | -0.500 | ⚠️ NO |
| COA | enrollment_count | 32.70 | 47.00 | 84.21 | -0.038 | ✅ |
| COA | graduation_count | 9.48 | 1.00 | 7.83 | -411.709 | ⚠️ NO |
| COC | enrollment_count | 48.73 | 64.33 | 151.51 | 0.255 | ✅ |
| COC | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| COE | enrollment_count | 58.26 | 75.33 | 187.14 | 0.271 | ✅ |
| COE | graduation_count | 1.19 | 0.33 | 0.52 | -7.121 | ⚠️ NO |
| COED | enrollment_count | 101.74 | 131.33 | 319.77 | 0.271 | ✅ |
| COED | graduation_count | 10.61 | 8.00 | 7.24 | -13.127 | ⚠️ NO |
| CPADM | enrollment_count | 38.35 | 49.33 | 125.07 | 0.286 | ✅ |
| CPADM | graduation_count | 0.00 | 0.00 | 0.00 | 1.000 | ⚠️ NO |
| IPE | enrollment_count | 30.50 | 47.00 | 24.84 | -1.109 | ⚠️ NO |
| IPE | graduation_count | 10.06 | 10.33 | 16.81 | -3.133 | ✅ |
