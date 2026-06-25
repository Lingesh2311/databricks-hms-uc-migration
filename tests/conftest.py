import os
import sys

# Make scripts/ importable so tests can `from scanner import ...`.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
