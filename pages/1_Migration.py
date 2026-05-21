"""
pages/1_Migration.py
Imports and renders the migration page module.
"""
import sys
import os

# Make sure migration_page.py (in root) is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from migration_page import render

render()
