#!/usr/bin/env python3
"""
main.py
=======
Entry point for the Matrix Ecosystem Security and Wellness Monitor bot.

Usage:
    python3 main.py

Ensure you have copied .env.example to .env and filled in all required values
before running this script.
"""

import sys
import os

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.bot import main

if __name__ == "__main__":
    main()
