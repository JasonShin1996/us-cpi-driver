import os
import sys

# the detail/ modules import each other by bare name (they are run as scripts)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
