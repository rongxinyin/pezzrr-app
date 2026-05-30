"""Setup file for Dashboard Command Agent."""

from setuptools import setup, find_packages

MAIN_MODULE = "agent"

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="dashboard_command",
    version="1.0.0",
    description="Dashboard command bridge agent for PEZZRR Controller",
    author="PEZZRR",
    packages=find_packages("."),
    entry_points={
        "setuptools.installation": [
            "eggsecutable = dashboard_command." + MAIN_MODULE + ":main",
        ]
    },
    install_requires=requirements,
    python_requires=">=3.10",
)
