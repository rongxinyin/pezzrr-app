"""
Setup file for Ecobee Agent
"""

from setuptools import setup, find_packages

MAIN_MODULE = 'ecobee'

# Read requirements
with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='ecobee_agent',
    version='1.0.0',
    description='Ecobee Smart Thermostat Agent for PEZZRR Controller',
    author='PEZZRR',
    packages=find_packages('.'),
    entry_points={
        'setuptools.installation': [
            # 'eggsecutable = ' + MAIN_MODULE + '_agent:main',
            'eggsecutable = ' + MAIN_MODULE + '_agent:main',
        ]
    },
    install_requires=requirements,
    python_requires='>=3.8',
)