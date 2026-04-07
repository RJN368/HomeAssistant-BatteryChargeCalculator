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

VERSION = "3.3.7"

DOMAIN = "battery_charge_calculator"
TITLE = "Battery Charge Calculator"
BATTERY_SCHEDULE_DATA_ID = "battery_schedule"
BATTERY_PROJECTION_SENSOR = "battery_charge_calculator.battery_projection_sensor"
BATTERY_PROJECTION_SENSOR_NAME = "Battery Charge Projection"
BATTERY_CHARGE_SENSOR = "battery_charge_calculator.battery_charge_slots_sensor"
CHARGE_COST_ESTIMATE_SENSOR = "battery_charge_calculator.cost_prediction_sensor"
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
ATTR_ENABLED = "enabled"
