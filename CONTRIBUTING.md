

# Contributing to Home Assistant Battery Charge Calculator

Thank you for your interest in contributing! This addon optimizes battery charge/discharge/export schedules using Octopus Energy rates, weather data, solar gain, and GivEnergy integration.

## How to Contribute

- Bug fixes, new features, and improvements are welcome!
- Areas for contribution include:
	- Scheduling and optimization algorithms
	- Weather/load/solar data integration
	- GivEnergy API integration
	- Documentation and examples

## Development Setup

1. Clone the repository
2. (Optional) Set up a Python virtual environment
3. Install dependencies:

	 ```bash
	 pip install -r requirements.test.txt
	 ```

## Running Tests

Tests use `pytest`. To run all tests:

```bash
python -m pytest tests
```

Some tests may require valid Octopus Energy API credentials. Set the following environment variable if needed:

```bash
export OCTOPUS_API_KEY=your_api_key_here
```

## Code Style

- Follow the Home Assistant core guidelines (PEP8, type hints, async best practices)
- Run `ruff` and `pylint` before submitting a PR

## Submitting Changes

1. Fork the repository and create your branch
2. Make your changes with clear commit messages
3. Ensure all tests pass
4. Open a pull request with a description of your changes

Thank you for helping improve the Battery Charge Calculator!
