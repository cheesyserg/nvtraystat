import sys
import os
import subprocess
from PyQt6.QtCore import Qt, QTimer, QFileInfo, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, 
                             QFileIconProvider, QMessageBox)

try:
    import pynvml
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False

# --- CONFIGURATION ---
GPU_PCI_ADDR = "0000:01:00.0"
RUNTIME_STATUS_PATH = f"/sys/bus/pci/devices/{GPU_PCI_ADDR}/power/runtime_status"

STATUS_POLL_MS = 1000
FAST_CHECK_COUNT = 5
FAST_CHECK_INTERVAL_MS = 2000
IDLE_CHECK_LOOP_MS = 10000

ICON_ACTIVE = "icons/active.png"
ICON_IDLE = "icons/idle.png"
ICON_SUSPENDED = "icons/suspended.png"
ICON_ERROR = "dialog-error"

class GPUWorker(QObject):
    data_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.handle = None
        self._is_initialized = False

    def _ensure_init(self):
        """Method 1: Dynamically initialize NVML only when needed."""
        if not NVML_AVAILABLE: return False
        if not self._is_initialized:
            try:
                pynvml.nvmlInit()
                self.handle = pynvml.nvmlDeviceGetHandleByPciBusId(GPU_PCI_ADDR.encode())
                self._is_initialized = True
                print("NVML Initialized (GPU Wake)")
            except Exception as e:
                print(f"NVML Init Error: {e}")
                return False
        return True

    def _shutdown(self):
        """Properly release handles to allow hardware suspension."""
        if self._is_initialized:
            try:
                pynvml.nvmlShutdown()
                self._is_initialized = False
                self.handle = None
                print("NVML Shutdown (Allowing Suspend)")
            except: pass

    def fetch_update(self):
        """Optimization 3: Batch queries, but only if NVML can init."""
        if not self._ensure_init(): return

        data = {"util": "0%", "mem": "0MiB", "procs": []}
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(self.handle)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
            data["util"] = f"{util.gpu}%"
            data["mem"] = f"{mem_info.used // (1024**2)}MiB"

            # Merge process types
            procs = (pynvml.nvmlDeviceGetComputeRunningProcesses(self.handle) + 
                     pynvml.nvmlDeviceGetGraphicsRunningProcesses(self.handle))
            
            seen_pids = set()
            for p in procs:
                if p.pid not in seen_pids:
                    try:
                        name = pynvml.nvmlSystemGetProcessName(p.pid)
                        data["procs"].append((str(p.pid), name))
                        seen_pids.add(p.pid)
                    except: continue
            
            self.data_ready.emit(data)
        except Exception as e:
            self.error_occurred.emit(str(e))

class SystemTrayApp(QSystemTrayIcon):
    def __init__(self):
        super().__init__()
        self.state = "UNKNOWN"
        self.last_data = {"util": "0%", "mem": "0MiB", "procs": []}
        self.icon_provider = QFileIconProvider()
        self.fast_check_counter = 0

        self.worker_thread = QThread()
        self.worker = GPUWorker()
        self.worker.moveToThread(self.worker_thread)
        self.worker.data_ready.connect(self.handle_worker_data)
        self.worker_thread.start()

        self.menu = QMenu()
        self.metrics_action = QAction("Initializing...", self)
        self.metrics_action.setEnabled(False)
        self.menu.addAction(self.metrics_action)
        self.menu.addSeparator()
        self.task_mgr_separator = self.menu.addSeparator()

        self.task_manager_action = QAction(QIcon.fromTheme("utilities-system-monitor"), "GPU Task Manager", self)
        self.task_manager_action.triggered.connect(self.open_task_manager)
        self.menu.addAction(self.task_manager_action)
        
        self.quit_action = QAction(QIcon.fromTheme("application-exit"), "Quit", self)
        self.quit_action.triggered.connect(QApplication.instance().quit)
        self.menu.addAction(self.quit_action)

        self.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self.update_process_menu)

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.check_hardware_status)
        self.status_timer.start(STATUS_POLL_MS)

        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.worker.fetch_update)

        self.check_hardware_status(startup=True)

    def handle_worker_data(self, data):
        self.last_data = data
        if self.state == "ACTIVE" and not data["procs"]:
            self.set_state("IDLE_DETECTING")
        elif self.state == "IDLE_DETECTING":
            if data["procs"]:
                self.set_state("ACTIVE")
            elif self.fast_check_counter > 0:
                self.fast_check_counter -= 1
                if self.fast_check_counter <= 0:
                    # Shutdown NVML here to let the kernel suspend the device
                    self.worker._shutdown()
                    self.idle_timer.start(IDLE_CHECK_LOOP_MS)
        self.update_ui_text()

    def check_hardware_status(self, startup=False):
        try:
            with open(RUNTIME_STATUS_PATH, 'r') as f:
                status = f.read().strip()

            if startup:
                self.set_state("SUSPENDED" if status == "suspended" else "IDLE_DETECTING")
                return

            if status == "suspended" and self.state != "SUSPENDED":
                self.worker._shutdown() # Ensure NVML is off
                self.set_state("SUSPENDED")
            elif status == "active" and self.state == "SUSPENDED":
                self.set_state("IDLE_DETECTING")

            if self.state != "SUSPENDED":
                # Only poll NVML if we aren't in the long idle loop
                if not self.idle_timer.isActive():
                    self.worker.fetch_update()
        except:
            self.set_state("ERROR")

    def set_state(self, new_state):
        if self.state == new_state: return
        print(f"State: {self.state} -> {new_state}")
        self.state = new_state
        self.idle_timer.stop()

        if new_state == "IDLE_DETECTING":
            self.fast_check_counter = FAST_CHECK_COUNT
            icon = ICON_IDLE
        elif new_state == "ACTIVE":
            icon = ICON_ACTIVE
        elif new_state == "SUSPENDED":
            icon = ICON_SUSPENDED
            self.last_data = {"util": "0%", "mem": "0MiB", "procs": []}
        else:
            icon = ICON_ERROR

        self.apply_icon(icon)

    def apply_icon(self, path):
        icon = QIcon(path) if os.path.exists(path) else QIcon.fromTheme(path)
        self.setIcon(icon)

    def update_ui_text(self):
        txt = f"Utilization: {self.last_data['util']} | Memory: {self.last_data['mem']}"
        self.metrics_action.setText(txt.center(60))
        st = "IDLE" if self.state == "IDLE_DETECTING" else self.state
        self.setToolTip(f"GPU: {st}\n{txt}")

    def update_process_menu(self):
        actions = self.menu.actions()
        metrics_idx = actions.index(self.metrics_action)
        sep_idx = actions.index(self.task_mgr_separator)
        for act in actions[metrics_idx + 1 : sep_idx]:
            self.menu.removeAction(act)

        if self.state in ["ACTIVE", "IDLE_DETECTING"] and self.last_data["procs"]:
            for pid, name in reversed(self.last_data["procs"]):
                try:
                    exe = os.readlink(f"/proc/{pid}/exe")
                    icon = self.icon_provider.icon(QFileInfo(exe))
                except: icon = QIcon.fromTheme("application-x-executable")
                act = QAction(icon, f"[{pid}] {name}", self, enabled=False)
                self.menu.insertAction(self.task_mgr_separator, act)
            self.menu.insertSeparator(self.menu.actions()[metrics_idx + 1])
        else:
            msg = "GPU Suspended" if self.state == "SUSPENDED" else "No active processes"
            self.menu.insertAction(self.task_mgr_separator, QAction(msg, self, enabled=False))

    def open_task_manager(self):
        script = os.path.join(os.path.dirname(__file__), "gpu_task_manager.py")
        subprocess.Popen([sys.executable, script])

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = SystemTrayApp()
    tray.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
