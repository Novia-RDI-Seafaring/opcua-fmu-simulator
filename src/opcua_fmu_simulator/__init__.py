from .operations import ops
from .experiment_controller import TestSystem
import asyncio
import sys
import os

EXPERIMENTS_DIR = "experiments/"

def main():
    """Main entry point for running experiments."""
    args = sys.argv[1:]
    if not args:
        print("Running all experiments")
        experiment_configs = [
            os.path.join(EXPERIMENTS_DIR, f)
            for f in os.listdir(EXPERIMENTS_DIR)
            if os.path.isfile(os.path.join(EXPERIMENTS_DIR, f))
        ]
    else:
        print("Running specific experiments")
        experiment_configs = [f"{EXPERIMENTS_DIR}/{file_name}" for file_name in args]

    async def run_experiments():
        experiments = TestSystem(experiment_configs=experiment_configs)
        await experiments.main_testing_loop()
    
    asyncio.run(run_experiments())

