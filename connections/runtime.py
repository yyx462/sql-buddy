"""Runtime environment detection.

Decides whether a connection's `host` should be rewritten to the Windows host
IP: when sql-buddy runs inside WSL but the user's tunnel is on the Windows side,
the driver must connect to the Windows host rather than `localhost`.
"""

import os
from typing import Optional


def is_wsl() -> bool:
    if is_container():
        return False
    try:
        with open('/proc/version', 'r') as f:
            version = f.read().lower()
            return 'microsoft' in version or 'wsl' in version
    except (FileNotFoundError, IOError):
        return False


def is_container() -> bool:
    if os.path.exists('/.dockerenv'):
        return True
    try:
        with open('/proc/1/cgroup', 'r') as f:
            cgroup = f.read()
            if any(sig in cgroup for sig in ['docker', 'containerd', 'kubepods', 'lxc']):
                return True
    except (FileNotFoundError, IOError):
        pass
    for var in ['DOCKER_CONTAINER', 'DOCKER_ID', 'CONTAINER', 'KUBERNETES_SERVICE_HOST']:
        if os.environ.get(var):
            return True
    return False


def get_windows_host_ip() -> Optional[str]:
    if not is_wsl():
        return None
    try:
        with open('/etc/resolv.conf', 'r') as f:
            for line in f:
                if line.strip().startswith('nameserver'):
                    ip = line.split()[1]
                    if ip and ip != '127.0.0.1':
                        return ip
    except (FileNotFoundError, IOError):
        pass
    return None
