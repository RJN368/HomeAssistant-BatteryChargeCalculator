"""ML sub-package for battery_charge_calculator.

Provides machine-learning based consumption prediction as a residual
correction layer on top of the physics model (D-1).  All public classes
are exported from sub-modules:

  ml/sources/   — external API data ingestion (HistoricalDataSource protocol)
  ml/data_pipeline.py     — DataFrame construction and feature engineering
  ml/model_trainer.py     — sklearn Pipeline, training, and prediction
  ml/model_persistence.py — atomic joblib save/load

The entire ml/ package is opt-in; it is only activated when ML_ENABLED is
True in the integration config.  Any ImportError from optional dependencies
(scikit-learn, pandas) must be caught at the call site in coordinators.py,
following the guard pattern defined in D-16.
"""
