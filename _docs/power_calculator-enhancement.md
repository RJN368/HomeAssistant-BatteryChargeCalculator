# PowerCalculator Enhancement: Heat Load & Heat Loss

## Overview
This enhancement enables the PowerCalculator to account for whether the house is heated with electricity and, if so, to calculate the heat load for any given temperature. If the house is not electrically heated, a standard base load is used. If the user cannot provide the heat loss value, the system will estimate it based on home characteristics.

## Feature Logic

1. **User Input: Heating Type & Efficiency**
    - Prompt: What type of heating system do you have?
       - Options: Electric resistance, Heat pump, Gas, Other
    - If electric resistance or heat pump:
       - Prompt for system efficiency (COP — Coefficient of Performance)
          - Default COP: 1.0 for electric resistance, 3.0 for heat pump (user can override)
    - If not electric heating: Use a standard day-to-day base load profile.

2. **Heat Load Calculation (Electric Heating)**
   - If user provides heat loss value, use it directly.
   - If not, estimate heat loss using:
     - Home size (m² or ft²)
     - Age/type of construction (insulation quality)
     - Number of external walls/windows
     - Typical indoor temperature
     - Outdoor temperature (from weather data)
   - Use standard formulas or lookup tables to estimate heat loss.

3. **Base Load Calculation (Non-Electric Heating)**
   - Use the existing base consumption profile.
   - Optionally allow user to customize or override the base load.

4. **Integration**
   - PowerCalculator accepts user configuration for heating type and (optionally) heat loss.
   - If heat loss is not provided, prompt for additional home details and estimate it.
   - Use weather data to calculate the heating load for each time slot.
   - Add base load and solar gain as before.

## User Experience
- During setup, user is asked if the home is electrically heated.
- If yes, user is prompted for heat loss or, if unknown, for home details to estimate it.
- The system uses this information to optimize the battery schedule for comfort and cost.

## Formula

**Formulas:**
```
Heat Load = Heat Loss × (Indoor Temp - Outdoor Temp)
Electrical Power Required = Heat Load / COP
```

COP (Coefficient of Performance) reflects heating system efficiency:
   - Electric resistance: COP = 1.0
   - Heat pump: COP ≈ 3.0 (varies by model and temperature)
   - User can override default COP for custom systems

## Next Steps
- Review and refine this logic and documentation.
- Decide on user prompts and configuration flow.
- Discuss any additional variables or edge cases to support.
