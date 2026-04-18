TITLE = "Battery Charge Calculator"
import voluptuous as vol

cv_float = vol.All(vol.Coerce(float))
GIVENERGY_SERIAL_NUMBER = "givenergy_api_serial_number"
GIVENERGY_API_TOKEN = "givenergy_api_token"
OCTOPUS_MPN = "octopus_mpn"
OCTOPUS_ACCOUNT_NUMBER = "octopus_account_number"
OCTOPUS_APIKEY = "octopus_api_key"
OCTOPUS_EXPORT_MPN = "octopus_export_mpn"
SIMULATE_ONLY = "simulate_only"
INVERTER_SIZE_KW = "inverter_size_kw"
INVERTER_EFFICIENCY = "inverter_efficiency"
BATTERY_CAPACITY_KWH = "battery_capacity_kwh"

DEFAULT_INVERTER_SIZE_KW = 3.6
DEFAULT_INVERTER_EFFICIENCY = 0.9
DEFAULT_BATTERY_CAPACITY_KWH = 9.0

# Heating / PowerCalculator configuration
HEATING_TYPE = "heating_type"
HEATING_COP = "heating_cop"
HEATING_HEAT_LOSS = "heating_heat_loss"
HEATING_INDOOR_TEMP = "heating_indoor_temp"
HEATING_FLOW_TEMP = "heating_flow_temp"
HEATING_KNOWN_POINTS = "heating_known_points"

# Valid heating types: 'none', 'interpolation', 'electric'
HEATING_TYPE_NONE = "none"
HEATING_TYPE_INTERPOLATION = "interpolation"
HEATING_TYPE_ELECTRIC = "electric"
HEATING_TYPE_HEATPUMP = "heatpump"  # internal / legacy only — not shown in UI
HEATING_TYPES = [
    HEATING_TYPE_NONE,
    HEATING_TYPE_INTERPOLATION,
    HEATING_TYPE_ELECTRIC,
]
DEFAULT_HEATING_TYPE = HEATING_TYPE_INTERPOLATION
DEFAULT_HEATING_COP = 1.0
DEFAULT_HEATING_INDOOR_TEMP = 20.0
DEFAULT_HEATING_FLOW_TEMP = 45.0  # °C — typical radiator flow temperature (A7/W45)
DEFAULT_HEATING_HEAT_LOSS = 0.0  # 0.0 means "not set"
DEFAULT_HEATING_KNOWN_POINTS = ""  # empty string means use built-in defaults

# Heat-loss configuration method (used in the options flow)
HEAT_LOSS_METHOD = "heat_loss_method"
HEAT_LOSS_METHOD_KNOWN = "known"  # user enters W/°C directly
HEAT_LOSS_METHOD_REPORT = (
    "report"  # user enters W at design temp (from a heat loss report)
)
HEAT_LOSS_METHOD_ESTIMATE = "estimate"  # user answers building questions

# Heat loss report fields (used when method == report)
HEAT_LOSS_REPORT_WATTS = "heat_loss_report_watts"
HEAT_LOSS_REPORT_OUTDOOR_TEMP = "heat_loss_report_outdoor_temp"
HEAT_LOSS_REPORT_INDOOR_TEMP = "heat_loss_report_indoor_temp"
DEFAULT_HEAT_LOSS_REPORT_WATTS = 0.0
DEFAULT_HEAT_LOSS_REPORT_OUTDOOR_TEMP = -3.0  # °C — typical UK design temp
DEFAULT_HEAT_LOSS_REPORT_INDOOR_TEMP = 20.0  # °C

# Base load configuration
BASE_LOAD_KWH_30MIN = "base_load_kwh_30min"
DEFAULT_BASE_LOAD_KWH_30MIN = 0.25  # kWh per 30-min slot default

# Building estimation fields (used when heat_loss is 0)
BUILDING_FLOOR_AREA = "building_floor_area"
BUILDING_AGE = "building_age"
BUILDING_WALL_TYPE = "building_wall_type"
BUILDING_GLAZING = "building_glazing"

BUILDING_WALL_TYPES = [
    "solid_uninsulated",
    "solid_insulated",
    "cavity_uninsulated",
    "cavity_insulated",
    "modern_insulated",
]

DEFAULT_BUILDING_FLOOR_AREA = 100.0
DEFAULT_BUILDING_AGE = "1930_1975"
DEFAULT_BUILDING_WALL_TYPE = "cavity_uninsulated"
DEFAULT_BUILDING_GLAZING = "double"

VERSION = "3.3.7"
DOMAIN = "battery_charge_calculator"
BATTERY_SCHEDULE_DATA_ID = "battery_schedule"
BATTERY_PROJECTION_SENSOR = "battery_charge_calculator.battery_projection_sensor"
BATTERY_PROJECTION_SENSOR_NAME = "Battery Charge Projection"
BATTERY_CHARGE_SENSOR = "battery_charge_calculator.battery_charge_slots_sensor"
BATTERY_CHARGE_SENSOR_NAME = "Battery Charge Slots"
CHARGE_COST_ESTIMATE_SENSOR = "battery_charge_calculator.cost_prediction_sensor"
EST_POWER_DEMAND_SENSOR = "battery_charge_calculator.est_power_demand"
EST_POWER_DEMAND_SENSOR_NAME = "Estimated Power Demand"
CHARGE_SWITCH_NAME = "Charge Switch"
CHARGE_SWITCH_ID = "charge_switch"
EVENT_TIMER_FINISHED = "scheduler_timer_finished"
EVENT_TIMER_UPDATED = "scheduler_timer_updated"
EVENT_ITEM_UPDATED = "scheduler_item_updated"
EVENT_ITEM_CREATED = "scheduler_item_created"
EVENT_ITEM_REMOVED = "scheduler_item_removed"
EVENT_STARTED = "schedule_started"

STATE_INIT = "init"
STATE_READY = "ready"
STATE_COMPLETED = "completed"


ATTR_START = "start"
ATTR_STOP = "stop"
ATTR_TIMESLOTS = "timeslots"

# ─────────────────────────── ML Power Estimation ─────────────────────────────
# Feature introduced 2026-04-10. All keys default to safe values so existing
# config entries without ML settings continue to work unchanged (D-16).

# Master switch — defaults to False so ML is never activated unexpectedly
ML_ENABLED = "ml_enabled"
DEFAULT_ML_ENABLED = False

# ML Service connection
ML_SERVICE_URL = "ml_service_url"
ML_SERVICE_API_KEY = "ml_service_api_key"
ML_SERVICE_TLS_FINGERPRINT = (
    "ml_service_tls_fingerprint"  # optional; enables cert-pinning
)

DEFAULT_ML_SERVICE_URL = ""
DEFAULT_ML_SERVICE_API_KEY = ""
DEFAULT_ML_SERVICE_TLS_FINGERPRINT = ""

# Data source selection
ML_CONSUMPTION_SOURCE = "ml_consumption_source"

# Valid values for ML_CONSUMPTION_SOURCE
ML_CONSUMPTION_SOURCE_GIVENERGY = "givenergy"
ML_CONSUMPTION_SOURCE_OCTOPUS = "octopus"
ML_CONSUMPTION_SOURCE_BOTH = "both"

# Defaults
DEFAULT_ML_CONSUMPTION_SOURCE = ML_CONSUMPTION_SOURCE_GIVENERGY

# Octopus meter serial — optional, only needed for Octopus consumption source.
# Distinct from MPAN. Found on the Octopus account page or auto-discovered.
OCTOPUS_METER_SERIAL = "octopus_meter_serial"
DEFAULT_OCTOPUS_METER_SERIAL = ""

# Training configuration
ML_TRAINING_LOOKBACK_DAYS = "ml_training_lookback_days"
DEFAULT_ML_TRAINING_LOOKBACK_DAYS = 730  # days of historical data to fetch

# Sensor identifiers
ML_MODEL_STATUS_SENSOR = "battery_charge_calculator.ml_model_status"
ML_MODEL_STATUS_SENSOR_NAME = "ML Power Model Status"
ANNUAL_FORECAST_SENSOR = "battery_charge_calculator.annual_forecast"

# Last recalculation sensor
LAST_RECALCULATION_SENSOR = "battery_charge_calculator.last_recalculation"
LAST_RECALCULATION_SENSOR_NAME = "Charge Slots Last Recalculated"

# Recalculation reason values
REPLAN_REASON_INITIAL_SETUP = "initial_setup"
REPLAN_REASON_NO_PLAN = "no_plan"
REPLAN_REASON_PLAN_EXPIRING = "plan_expiring"
REPLAN_REASON_BATTERY_DEVIATION = "battery_deviation"
REPLAN_REASON_NO_ACTIVE_SLOT = "no_active_slot"
REPLAN_REASON_MANUAL = "manual"
ANNUAL_FORECAST_SENSOR_NAME = "Annual Energy Forecast"
DAILY_POWER_FORECAST_SENSOR = "battery_charge_calculator.daily_power_forecast"
DAILY_POWER_FORECAST_SENSOR_NAME = "Daily Power Forecast"
ML_POWER_SURFACE_SENSOR = "battery_charge_calculator.ml_power_surface"
ML_POWER_SURFACE_SENSOR_NAME = "ML Power Surface"

# ─────────────────────────── Tariff Comparison ───────────────────────────────
# Feature introduced 2026-04-16.  All keys default to safe values so existing
# config entries without tariff comparison settings continue to work unchanged.

# Master switch — defaults to False so the feature is never activated unexpectedly
TARIFF_COMPARISON_ENABLED = "tariff_comparison_enabled"

# Tariff list — stored as a JSON string (list of tariff objects per §4.2)
TARIFF_COMPARISON_TARIFFS = "tariff_comparison_tariffs"

# Update / cache age thresholds (integer days)
TARIFF_COMPARISON_UPDATE_INTERVAL_DAYS = "tariff_comparison_update_interval_days"
TARIFF_COMPARISON_CACHE_MAX_AGE_DAYS = "tariff_comparison_cache_max_age_days"

# Whether to include export earnings in the comparison (False until OQ-1 resolved)
TARIFF_COMPARISON_INCLUDE_EXPORT = "tariff_comparison_include_export"

# Defaults
DEFAULT_TARIFF_COMPARISON_UPDATE_INTERVAL_DAYS = 7
DEFAULT_TARIFF_COMPARISON_CACHE_MAX_AGE_DAYS = 7
DEFAULT_TARIFF_COMPARISON_INCLUDE_EXPORT = False

# Sensor identifiers
TARIFF_COMPARISON_SENSOR = "battery_charge_calculator.tariff_comparison"
TARIFF_COMPARISON_SENSOR_NAME = "Monthly Tariff Comparison"

# Export meter serial — new field; added alongside OCTOPUS_METER_SERIAL (D-18)
OCTOPUS_EXPORT_METER_SERIAL = "octopus_export_meter_serial"

# Solar production entity — optional entity_id of a cumulative kWh sensor
# (e.g. sensor.solar_energy_production).  When set, historical solar data is
# fetched from HA's long-term statistics and used in tariff comparison simulations
# so the GeneticEvaluator accounts for solar generation on each simulated day.
SOLAR_ENERGY_ENTITY = "solar_energy_entity"
DEFAULT_SOLAR_ENERGY_ENTITY = ""
