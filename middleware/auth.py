# shim to reuse backend middleware auth
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BASE_DIR)

if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from backend.middleware.auth import *  # noqa

