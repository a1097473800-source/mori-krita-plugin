"""Krita entry point. Each process keeps its original release until it exits."""
from .native_loader import active_package

__path__[:] = [str(active_package())]
from . import startup
