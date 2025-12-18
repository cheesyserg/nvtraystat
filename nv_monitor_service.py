import sys
import subprocess
import os
from PyQt6.QtCore import Qt, QTimer, QFileInfo
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, QMainWindow,
                             QMessageBox, QFileIconProvider)

from gpu_task_manager import GpuTaskManager

# --- CONFIGURATION ---

# The sysfs path to the GPU's power state; reading this file does not wake the GPU.
RUNTIME_STATUS_PATH = "/sys/bus/pci/devices/0000:01:00.0/power/runtime_status"

# Standard NVIDIA-SMI command to query active compute applications.
NVIDIA_SMI_PROCESS_COMMAND = ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"]

# Standard NVIDIA-SMI command to query hardware utilization and memory metrics.
NVIDIA_SMI_STATUS_COMMAND = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader"]

# A high-performance shell pipeline that finds PIDs accessing NVIDIA device nodes via /proc.
PROC_QUERY_CMD = (
    "find /proc/[0-9]*/fd -lname '/dev/nvidia*' 2>/dev/null | "
    "awk -F/ '!seen[$3]++ { print $3 }'"
)

# Frequency (in milliseconds) for the master monitoring loop.
STATUS_POLL_MS = 1000   

# The number of consecutive idle cycles required to verify the GPU can safely enter a passive state.
STABILITY_THRESHOLD = 5 

# Pathing for custom status icons.
ICON_ACTIVE = "icons/active.png"
ICON_IDLE = "icons/idle.png"
ICON_SUSPENDED = "icons/suspended.png"
ICON_ERROR = "dialog-error"

class SystemTrayApp(QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "UNKNOWN"
        self.gpu_status_data = {"utilization": "N/A", "memory_used": "N/A"}
        self.icon_provider = QFileIconProvider()
        self.tm_window = None 
        self.last_proc_pids = set()
        self.stability_counter = 0

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.monitor_logic_flow)

        self.activated.connect(self.on_activated)

        self.menu = QMenu()
        
        self.metrics_action = QAction("Utilization: N/A | Memory: N/A", self)
        self.metrics_action.setEnabled(False)
        
        self.tm_btn = QAction(QIcon.fromTheme("utilities-system-monitor"), "Open GPU Task Manager", self)
        self.tm_btn.triggered.connect(self.launch_tm)
        
        self.quit_action = QAction(QIcon.fromTheme("application-exit"), "Quit", self)
        self.quit_action.triggered.connect(QApplication.instance().quit)

        self.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self.refresh_menu_list)

        self.monitor_logic_flow(startup_check=True)
        self.status_timer.start(STATUS_POLL_MS)

    def on_activated(self, reason):
        """Handle tray icon activation (e.g., left-click to open Task Manager)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.launch_tm()

    def launch_tm(self):
        """Opens or raises the GPU Task Manager window."""
        if not self.tm_window: self.tm_window = GpuTaskManager()
        self.tm_window.show()
        self.tm_window.raise_()

    def set_state(self, new_state):
        """Updates internal state and refreshes icon."""
        if self.state != new_state:
            old_state = self.state
            self.state = new_state
            if new_state == "SUSPENDED":
                self.set_icon(ICON_SUSPENDED)
                self.gpu_status_data = {"utilization": "0%", "memory_used": "0MiB"}
                self.last_proc_pids = set()
            elif new_state == "IDLE":
                self.set_icon(ICON_IDLE)
            elif new_state == "ACTIVE":
                self.set_icon(ICON_ACTIVE)
            
            curr = self.icon(); self.setIcon(QIcon()); self.setIcon(curr)
            print(f"State transition: {old_state} -> {new_state}")
        self.update_tooltip()

    def set_icon(self, path):
        if os.path.exists(path): self.setIcon(QIcon(path))
        else: self.setIcon(QIcon.fromTheme(path))

    def monitor_logic_flow(self, startup_check=False):
        """Highly optimized gated monitoring loop."""
        try:
            with open(RUNTIME_STATUS_PATH, 'r') as f:
                hw_status = f.read().strip()
            
            if hw_status == "suspended":
                self.set_state("SUSPENDED")
                return

            proc_res = subprocess.run(PROC_QUERY_CMD, shell=True, capture_output=True, text=True, timeout=1)
            current_pids = set(proc_res.stdout.strip().split()) if proc_res.stdout.strip() else set()

            if current_pids != self.last_proc_pids:
                print(f"Activity Change Detected: {self.last_proc_pids} -> {current_pids}")
                self.last_proc_pids = current_pids
                self.stability_counter = 0

            if self.state == "ACTIVE" or self.stability_counter < STABILITY_THRESHOLD:
                smi_proc = subprocess.run(NVIDIA_SMI_PROCESS_COMMAND, capture_output=True, text=True, timeout=2)
                self.set_state("ACTIVE" if smi_proc.stdout.strip() else "IDLE")
                self.poll_hw_metrics()
                
                if self.state == "IDLE":
                    self.stability_counter += 1
            else:
                if self.stability_counter == STABILITY_THRESHOLD:
                    print("State stable. Silencing NVIDIA-SMI to allow suspend.")
                    self.stability_counter += 1
                
                self.metrics_action.setText("Utilization: 0% | Memory: 0MiB".center(60))

        except Exception as e:
            print(f"Error: {e}")
            self.set_state("ERROR")

    def poll_hw_metrics(self):
        try:
            res = subprocess.run(NVIDIA_SMI_STATUS_COMMAND, capture_output=True, text=True, timeout=1)
            parts = [p.strip() for p in res.stdout.strip().split(',')]
            if len(parts) == 2:
                self.gpu_status_data = {"utilization": parts[0], "memory_used": parts[1]}
                self.metrics_action.setText(f"Utilization: {parts[0]} | Memory: {parts[1]}".center(60))
        except: pass

    def update_tooltip(self):
        self.setToolTip(f"GPU: {self.state}\nUtil: {self.gpu_status_data['utilization']}\nMem: {self.gpu_status_data['memory_used']}")

    def get_process_icon(self, pid, name):
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
            icon = self.icon_provider.icon(QFileInfo(exe))
            if not icon.isNull(): return icon
        except: pass
        icon = QIcon.fromTheme(name.lower().split()[0])
        return icon if not icon.isNull() else QIcon.fromTheme("application-x-executable")

    def refresh_menu_list(self):
        """Rebuilds the menu visually."""
        self.menu.clear()
        
        self.menu.addAction(self.metrics_action)
        self.menu.addSeparator()

        if self.state == "SUSPENDED":
            a = QAction("GPU Suspended", self); a.setEnabled(False)
            self.menu.addAction(a)
        else:
            display = []
            for p in self.last_proc_pids:
                try:
                    with open(f"/proc/{p}/comm", "r") as f:
                        display.append([p, f.read().strip()])
                except: continue

            if display:
                for p, n in reversed(display):
                    icon = self.get_process_icon(p, n)
                    self.menu.addAction(QAction(icon, f"[{p}] {n}", self))
            else:
                a = QAction("No processes detected", self); a.setEnabled(False)
                self.menu.addAction(a)

        self.menu.addSeparator()
        self.menu.addAction(self.tm_btn)
        self.menu.addAction(self.quit_action)

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = SystemTrayApp()
    tray.show()
    sys.exit(app.exec())

if __name__ == '__main__': main()
