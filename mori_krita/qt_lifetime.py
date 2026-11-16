"""Qt can destroy a C++ child while Python still holds its wrapper."""
from PyQt5 import sip
from PyQt5.QtCore import QObject


def is_alive(value):
    """Check validity without invoking a method on a possibly deleted QObject."""
    return value is not None and (not isinstance(value, QObject) or not sip.isdeleted(value))
