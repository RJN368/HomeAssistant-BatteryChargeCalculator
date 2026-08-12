"""Genetic algorithm for optimizing battery charge/discharge schedule for Home Assistant battery charge calculator.

Contains Timeslot and GeneticEvaluator classes.
"""

import hashlib
import logging
import random

from .timeslot import Timeslot


class GeneticEvaluator:
    """Genetic algorithm for optimizing battery charge/discharge schedule."""

    def __init__(
        self,
        battery_start: float,
        standing_charge: float,
        inverter_size_kw: float = 3.6,
        inverter_efficiency: float = 0.9,
        battery_capacity_kwh: float = 9.0,
    ) -> None:
        """Initialize the evaluator with battery start and standing charge.

        Args:
            battery_start: Initial battery state of charge in kWh.
            standing_charge: Standing charge in pence to include in cost.
            inverter_size_kw: Inverter rated power in kW (default 3.6 kW).
            inverter_efficiency: Inverter round-trip efficiency as a fraction
                between 0 and 1 (default 0.9 = 90 %).
            battery_capacity_kwh: Usable battery capacity in kWh (default 9.0 kWh).

        The maximum energy that can be imported or exported in a single 30-minute
        slot is derived from the inverter parameters:
            max_per_slot_kwh = (inverter_size_kw * inverter_efficiency) / 2
        """
        self._logging = logging.getLogger(__name__)

        # Constants and inputs
        self.max_battery_capacity = battery_capacity_kwh
        self.max_charge_per_slot = (inverter_size_kw * inverter_efficiency) / 2
        self.max_discharge = (inverter_size_kw * inverter_efficiency) / 2
        self.population_size = 400
        self.generations = 700
        self.battery_start = battery_start
        self.standing_charge = standing_charge
        self.charge_options = ["charge", "export", "discharge"]
        self.num_slots = 0
        self.timeslots: list[Timeslot] = []
        self._forced_actions: list[str | None] = []

    def set_forced_actions(self, forced_actions: list[str | None] | None) -> None:
        """Set optional per-slot action constraints for schedule evaluation."""
        if not forced_actions:
            self._forced_actions = []
            return

        normalized: list[str | None] = []
        for action in forced_actions:
            if action is None:
                normalized.append(None)
            elif action in self.charge_options:
                normalized.append(action)
            else:
                self._logging.warning(
                    "Ignoring invalid forced action '%s'; expected one of %s",
                    action,
                    self.charge_options,
                )
                normalized.append(None)
        self._forced_actions = normalized

    def _forced_action_for_slot(self, index: int) -> str | None:
        """Return forced action for slot index, or None when unconstrained."""
        if index < 0 or index >= len(self._forced_actions):
            return None
        return self._forced_actions[index]

    def _apply_forced_actions(self, schedule: list[str]) -> list[str]:
        """Overlay forced actions on top of a candidate schedule."""
        constrained = list(schedule)
        for i in range(min(len(constrained), self.num_slots)):
            forced_action = self._forced_action_for_slot(i)
            if forced_action is not None:
                constrained[i] = forced_action
        return constrained

    def add_data(self, start_datetime, import_price, export_price, demand_in, solar_in):
        """Add a timeslot to the evaluation set."""
        demand = float(demand_in) if demand_in is not None else 0.0
        solar = float(solar_in) if solar_in is not None else 0.0
        self.timeslots.append(
            Timeslot(start_datetime, import_price, export_price, demand, solar)
        )
        self.num_slots = len(self.timeslots)

    def _evaluate_single_slot(self, timeslot: Timeslot, action: str, battery: float):
        """Evaluate a single timeslot for a given action and battery state."""
        if battery is None:
            battery = 0.0
        net_demand = timeslot.demand - timeslot.solar

        timeslot.initial_power = battery
        timeslot.charge_option = action
        timeslot.cost = 0

        if action == "charge":
            charge_amount = max(
                0.0,
                min(
                    self.max_charge_per_slot,
                    self.max_battery_capacity - battery + net_demand,
                ),
            )

            overflow = (
                battery
                + self.max_charge_per_slot
                - self.max_battery_capacity
                + net_demand
            )

            battery += charge_amount
            timeslot.cost = timeslot.import_price * charge_amount

            if overflow > 0:
                timeslot.cost = timeslot.cost + (timeslot.import_price * (overflow))

            # When solar exceeds demand the surplus fills the battery first;
            # anything that still can't fit is exported to the grid.
            if net_demand < 0:
                solar_surplus = -net_demand
                remaining_headroom = self.max_battery_capacity - battery
                solar_to_battery = min(solar_surplus, max(0.0, remaining_headroom))
                solar_exported = solar_surplus - solar_to_battery
                battery = min(battery + solar_to_battery, self.max_battery_capacity)
                timeslot.cost -= timeslot.export_price * solar_exported

        elif action == "export":
            # Discharge battery at inverter rate; home demand is met from the
            # discharge first, remainder goes to the grid.
            discharge_amount = min(self.max_discharge, battery)
            battery -= discharge_amount
            grid_export = max(0.0, discharge_amount - net_demand)
            # Any home demand not covered by battery discharge must be imported.
            shortfall = max(0.0, net_demand - discharge_amount)
            timeslot.cost = timeslot.export_price * grid_export * -1
            if shortfall > 0:
                timeslot.cost += timeslot.import_price * shortfall
        else:
            import_needed = max(0, net_demand - battery)
            battery -= min(battery, net_demand)

            if battery > self.max_battery_capacity:
                overflow = battery - self.max_battery_capacity
                battery = self.max_battery_capacity
                timeslot.cost = timeslot.export_price * overflow * -1
            else:
                timeslot.cost = timeslot.import_price * import_needed

        return battery

    # Fitness function to evaluate cost of a schedule
    def evaluate_schedule(self, schedule):
        """Calculate the total cost for a schedule."""
        net_cost = self.standing_charge
        battery = self.battery_start
        constrained_schedule = self._apply_forced_actions(schedule)

        for i, action in enumerate(constrained_schedule):
            battery = self._evaluate_single_slot(self.timeslots[i], action, battery)
            net_cost += self.timeslots[i].cost

        return net_cost

    # Initialize population with random actions
    def create_population(self):
        """Create the initial population of schedules."""
        population = []
        hash_lookup = {}
        conflict_count = 0
        ideal_schedule = self.create_ideal_schedule()
        population.append(ideal_schedule)
        hash_lookup[hashlib.md5(str(ideal_schedule).encode()).hexdigest()] = (
            ideal_schedule
        )

        export_schedule = self.create_export_ideal_schedule()
        export_hash = hashlib.md5(str(export_schedule).encode()).hexdigest()
        if export_hash not in hash_lookup:
            hash_lookup[export_hash] = export_schedule
            population.append(export_schedule)

        while len(population) < self.population_size:
            schedule = []
            for i in range(self.num_slots):
                forced_action = self._forced_action_for_slot(i)
                if forced_action is not None:
                    schedule.append(forced_action)
                elif self.timeslots[i].import_price <= 0:
                    schedule.append("charge")
                else:
                    schedule.append(random.choice(self.charge_options))

            array_string = str(schedule).encode()
            hash_value = hashlib.md5(array_string).hexdigest()

            if hash_value not in hash_lookup:
                hash_lookup[hash_value] = schedule
                population.append(schedule)
            else:
                conflict_count += 1
                if conflict_count > self.population_size * 2:
                    self._logging.warning(
                        "Too many hash conflicts when creating population; "
                        "this can occur when the number of unique schedules is less than "
                        "the population size. Proceeding with current population of %d schedules.",
                        self.population_size,
                    )
                    break

        return population

    def create_export_ideal_schedule(self):
        """Create a schedule that opportunistically favors strong export windows."""
        schedule = ["discharge" for _ in range(self.num_slots)]

        for i, slot in enumerate(self.timeslots):
            if slot.import_price <= 0:
                schedule[i] = "charge"
            elif slot.export_price > slot.import_price:
                schedule[i] = "export"

        return self._apply_forced_actions(schedule)

    def evaluate(self):
        """Run the genetic algorithm and return the best schedule and cost."""
        population = self.create_population()

        if len(population) < 2:
            return None, None

        for _ in range(self.generations):
            population = sorted(population, key=self.evaluate_schedule)

            parents = population[: self.population_size // 2]

            children = []
            while len(children) < self.population_size:
                parent1, parent2 = random.sample(parents, 2)
                crossover_point = random.randint(1, self.num_slots - 1)
                child = parent1[:crossover_point] + parent2[crossover_point:]

                if random.random() < 0.1:
                    unconstrained_slots = [
                        i
                        for i in range(self.num_slots)
                        if self._forced_action_for_slot(i) is None
                    ]
                    if unconstrained_slots:
                        mutation_point = random.choice(unconstrained_slots)
                        child[mutation_point] = random.choice(self.charge_options)

                child = self._apply_forced_actions(child)

                children.append(child)

            population = children

        population = sorted(population, key=self.evaluate_schedule)
        optimal_schedule = population[0]
        optimal_cost = self.evaluate_schedule(optimal_schedule)

        self._log_schedule(optimal_cost)

        return self.timeslots, optimal_cost

    def _log_schedule(self, optimal_cost) -> None:
        """Log the calculated charge/discharge plan as a readable table."""
        if not self.timeslots:
            self._logging.info("Schedule calculated: no timeslots")
            return

        lines = [
            f"Charge/discharge plan (total cost: £{optimal_cost:.4f}):",
            "  %-18s %-12s %8s %8s %8s %8s %8s %9s %12s %12s"
            % (
                "Time",
                "Action",
                "Batt kWh",
                "Agile p",
                "Export p",
                "Demand",
                "Solar",
                "Slot £",
                "Import Price £",
                "Export Price £",
            ),
            "  " + "-" * 113,
        ]
        for slot in self.timeslots:
            lines.append(
                "  %-18s %-12s %8.2f %8.2f %8.2f %8.2f %8.2f %9.4f %12.4f %12.4f"
                % (
                    slot.start_datetime_london().strftime("%d/%m %H:%M"),
                    slot.charge_option or "—",
                    slot.initial_power,
                    slot.import_price,
                    slot.export_price,
                    slot.demand,
                    slot.solar,
                    slot.cost,
                    slot.import_price,
                    slot.export_price,
                )
            )
        self._logging.info("\n".join(lines))

    def create_ideal_schedule(self):
        """Create a schedule based around charge/discharge options."""
        self._calculate_batterystate_from_index(0, self.battery_start)
        schedule = ["discharge" for _ in range(self.num_slots)]

        for i, _ in enumerate(self.timeslots):
            schedule[i] = "discharge"
            forced_action = self._forced_action_for_slot(i)
            if forced_action is not None:
                schedule[i] = forced_action
            elif self.timeslots[i].import_price <= 0:
                schedule[i] = "charge"
            elif self.timeslots[i].initial_power <= 0:
                charge_index = self._reverse_find_slot_with_headroom(i, schedule)

                self._calculate_batterystate_from_index(
                    charge_index,
                    self.timeslots[charge_index].initial_power
                    + self.max_charge_per_slot,
                )

                schedule[charge_index] = "charge"

        return self._apply_forced_actions(schedule)

    def _calculate_batterystate_from_index(self, index, battery):
        """Calculate battery state from a given index and battery value."""
        if battery is None:
            battery = 0.0
        for i in range(index, len(self.timeslots)):
            self.timeslots[i].initial_power = battery

            demand = (
                self.timeslots[i].demand
                if self.timeslots[i].demand is not None
                else 0.0
            )
            solar = (
                self.timeslots[i].solar if self.timeslots[i].solar is not None else 0.0
            )
            battery = battery - demand + solar

            battery = max(battery, 0)

    def _reverse_find_slot_with_headroom(self, indexPosition, schedule):
        """Find the furthest back slot with headroom for charging."""
        candidate = indexPosition

        while indexPosition > -1:
            if (
                self.timeslots[indexPosition].import_price
                < self.timeslots[candidate].import_price
                and schedule[indexPosition] != "charge"
            ):
                candidate = indexPosition

            if (
                self.timeslots[indexPosition].initial_power
                > self.max_battery_capacity - self.max_charge_per_slot
            ):
                indexPosition = indexPosition + 1
                break

            indexPosition = indexPosition - 1

        schedule[candidate] = "charge"

        return candidate
