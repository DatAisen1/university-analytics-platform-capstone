import pandas as pd
import sys
import types
from pipelines.common.errors import ModelEvaluationError, ModelTrainingError
from models.forecasting.train_prophet import fit_prophet, evaluate_all_series

class BrokenProphet:
    def fit(self, train_df):
        raise RuntimeError('boom')

sys.modules['prophet'] = types.SimpleNamespace(Prophet=BrokenProphet)
try:
    fit_prophet(pd.DataFrame({'ds': ['2021-01-01'], 'y_col': [1.0]}))
except ModelTrainingError as exc:
    print('training_error', exc.category.value, 'Prophet training failed' in str(exc))

import models.forecasting.train_prophet as train_mod
train_mod.load_series = lambda engine: pd.DataFrame({
    'college_id': ['CICT'], 'college_key': [1], 'period_ordinal': [1],
    'academic_year': [2021], 'semester_number': [1], 'enrollment_count': [1],
    'graduation_count': [1], 'ds': ['2021-01-01']
})
train_mod.walk_forward_evaluate = lambda college_series, metric: (_ for _ in ()).throw(RuntimeError('bad fold'))
try:
    evaluate_all_series(engine=None)
except ModelEvaluationError as exc:
    print('evaluation_error', exc.category.value, 'Walk-forward evaluation failed' in str(exc))
