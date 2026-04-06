"""Genetic algorithm for optimizing battery charge/discharge schedule for Home Assistant battery charge calculator.

Contains Timeslot and GeneticEvaluator classes.
"""

import hashlib
import logging
import random


class Timeslot:
    """A single timeslot for battery scheduling, holding all relevant data and results."""

    CHARGE_GAIN_30MINS = 1.65

    def __init__(
        self, start_datetime, import_price, export_price, demand_in, solar_in
    ) -> None:
        """Initialize a timeslot with all required parameters."""
        self._import_price = float(import_price) if import_price is not None else 0.0
        self._export_price = float(export_price) if export_price is not None else 0.0
        self._demand = float(demand_in) if demand_in is not None else 0.0
        self._solar = float(solar_in) if solar_in is not None else 0.0
        self._cost = 0
        self._charge_option = ""
        self._start_datetime = start_datetime
        self._initial_power = 0

    @property
    def start_datetime(self):
        """Get the start datetime for this timeslot."""
        return self._start_datetime

    @property
    def import_price(self):
        """Get the import price for this timeslot."""
        return self._import_price

    @property
    def export_price(self):
        """Get the export price for this timeslot."""
        return self._export_price

    @property
    def demand(self):
        """Get the demand value for this timeslot."""
        return self._demand

    @property
    def solar(self):
        """Get the solar value for this timeslot."""
        return self._solar

    @property
    def cost(self):
        """Get the cost for this timeslot."""
        return self._cost

    @cost.setter
    def cost(self, new_value):
        self._cost = new_value

    @property
    def charge_option(self):
        """Get the charge option for this timeslot."""
        return self._charge_option

    @charge_option.setter
    def charge_option(self, new_value):
        self._charge_option = new_value

    @property
    def initial_power(self):
        """Get the initial battery power for this timeslot."""
        return self._initial_power

    @initial_power.setter
    def initial_power(self, new_value):
        self._initial_power = new_value


class GeneticEvaluator:
    """Genetic algorithm for optimizing battery charge/discharge schedule."""

    def __init__(self, battery_start: float, standing_charge: float) -> None:
        """Initialize the evaluator with battery start and standing charge."""
        self._logging = logging.getLogger(__name__)

        # Constants and inputs
        self.max_battery_capacity = 9
        self.max_charge_per_slot = 1.44
        self.max_discharge = 2
        self.population_size = 400
        self.generations = 700
        self.battery_start = battery_start
        self.standing_charge = standing_charge
        self.charge_options = ["charge", "export", "discharge"]
        self.num_slots = 0
        self.timeslots: list[Timeslot] = []

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
            discharge_amount = min(self.max_discharge - net_demand, battery)
            battery = battery - discharge_amount
            timeslot.cost = (
                timeslot.export_price * (discharge_amount - net_demand)
            ) * -1

            overflow = self.max_discharge - discharge_amount

            if overflow > 0:
                timeslot.cost = timeslot.cost + (timeslot.import_price * overflow)
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

        for i, action in enumerate(schedule):
            battery = self._evaluate_single_slot(self.timeslots[i], action, battery)
            net_cost += self.timeslots[i].cost

        return net_cost

    # Initialize population with random actions
    def create_population(self):
        """Create the initial population of schedules."""
        population = []
        hash_lookup = {}

        population.append(self.create_ideal_schedule())

        while len(population) < min(self.num_slots, self.population_size):
            schedule = []
            for i in range(self.num_slots):
                if self.timeslots[i].import_price <= 0:
                    schedule.append("charge")
                else:
                    schedule.append(random.choice(self.charge_options))

            array_string = str(schedule).encode()
            hash_value = hashlib.md5(array_string).hexdigest()

            if hash_value not in hash_lookup:
                hash_lookup[hash_value] = schedule
                population.append(schedule)

        return population

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
                    mutation_point = random.randint(0, self.num_slots - 1)
                    child[mutation_point] = random.choice(self.charge_options)

                children.append(child)

            population = children

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
                    slot.start_datetime.strftime("%d/%m %H:%M"),
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
            if self.timeslots[i].import_price <= 0:
                schedule[i] = "charge"
            elif self.timeslots[i].initial_power <= 0:
                charge_index = self._reverse_find_slot_with_headroom(i, schedule)

                self._calculate_batterystate_from_index(
                    charge_index,
                    self.timeslots[charge_index].initial_power
                    + self.max_charge_per_slot,
                )

                schedule[charge_index] = "charge"

        return schedule

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
