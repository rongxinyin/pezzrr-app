"""
Setup file for EcoFlow Agent
"""

from setuptools import setup, find_packages

MAIN_MODULE = 'ecoflow'

# Read requirements
with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='ecoflow_agent',
    version='1.0.0',
    description='EcoFlow Battery Agent for PEZZRR Controller',
    author='PEZZRR',
    packages=find_packages('.'),
    entry_points={
        'setuptools.installation': [
            'eggsecutable = ' + MAIN_MODULE + '_agent:main',
        ]
    },
    install_requires=requirements,
    python_requires='>=3.8',
)
