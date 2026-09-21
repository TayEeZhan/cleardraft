"""Test-wide settings.

Unit tests must be hermetic: no network, no API spend, the same answer every
run. Now that a real ANTHROPIC_API_KEY lives in .env, any test that reaches the
model tier would make a live, billed call and pass or fail on the model's mood.

So the model tier is OFF for every test by default. The live model is exercised
deliberately and separately by scripts/run_challenge.py, not by pytest.
"""
import os

os.environ["CLEARDRAFT_USE_MODEL"] = "0"
