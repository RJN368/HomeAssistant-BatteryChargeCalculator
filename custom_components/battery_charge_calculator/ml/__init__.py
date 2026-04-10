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

scikit-learn is an optional dependency.  It is NOT listed in manifest.json
requirements because it cannot be installed on Python 3.14 in HA Core 2026.3+
(no binary wheels; source build requires Meson which is not available in the
HA environment).  When scikit-learn is absent SKLEARN_AVAILABLE is False and
all ML features degrade gracefully to disabled with a logged warning.
"""

try:
    import sklearn  # noqa: F401

    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
