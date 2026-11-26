import time
from nitrostack import health_check

class SystemHealthCheck:
    def __init__(self):
        self.start_time = time.time()

    @health_check("system")
    def check_system(self) -> bool:
        uptime = time.time() - self.start_time
        return uptime >= 0
