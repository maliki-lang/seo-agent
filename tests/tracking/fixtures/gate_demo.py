"""Gate-demo fixture notes.

These fixtures are for automated tests and the simulated-failure demo only.
They must never be loaded into production baselines or published reports.
"""

SIMULATE_FAILURE_EXAMPLE = "serper:authentication"

DEMO_COMMANDS = [
    "python -m data_sources.tracking.cli doctor --json",
    "python -m data_sources.tracking.cli daily --simulate-failure serper:authentication --json",
    "python -m data_sources.tracking.cli weekly --no-publish --json",
]
