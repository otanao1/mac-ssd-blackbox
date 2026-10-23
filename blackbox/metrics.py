"""Optional macOS per-process disk counters; resource provides CPU/peak RSS.

ABI: https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/resource.h
(rusage_info_v2: UUID, 10 process fields, 6 child fields, 2 disk byte fields).
"""
import ctypes
import os
import sys


class UsageV2(ctypes.Structure):
    _fields_ = [('uuid', ctypes.c_uint8 * 16), ('process_and_children', ctypes.c_uint64 * 16),
                ('read_bytes', ctypes.c_uint64), ('written_bytes', ctypes.c_uint64)]


def disk_io():
    if sys.platform != 'darwin':
        return None
    try:
        lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        function = lib.proc_pid_rusage
        function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        function.restype = ctypes.c_int
        usage = UsageV2()
        if function(os.getpid(), 2, ctypes.byref(usage)) != 0:
            return None
        return {'disk_read_bytes': usage.read_bytes, 'disk_written_bytes': usage.written_bytes}
    except (OSError, AttributeError):
        return None
