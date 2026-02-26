import os
import logging
from main import get_config

# os.environ["stev_smash_TEST_VAR"] = "prefixed_value"
# os.environ["TEST_VAR"] = "bare_value"

logging.basicConfig(level=logging.INFO)

print("--- Local .env Test ---")
print(f"LOGIN_URL: {get_config('LOGIN_URL')}")
print(f"LOGIN_USERNAME: {get_config('LOGIN_USERNAME')}")
print(f"TZ: {get_config('TZ')}")
print("-----------------------")
