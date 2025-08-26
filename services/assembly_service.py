# shim to reuse backend implementation
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BASE_DIR)

if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from backend.services.assembly_service import *  # noqa

