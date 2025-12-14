import sys
import subprocess
import os
from PyQt6.QtCore import Qt, QTimer, QFileInfo
from PyQt6.QtGui import QIcon, QAction, QCursor
from PyQt6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, QMainWindow,
                             QMessageBox, QFileIconProvider)
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

# --- CONFIGURATION ---
# Command to query compute processes (PID and name)
NVIDIA_SMI_PROCESS_COMMAND = ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"]
# Command to query overall GPU status (Utilization and Memory)
NVIDIA_SMI_STATUS_COMMAND = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader"]

# IMPORTANT: Replace 0000:01:00.0 with your actual NVIDIA GPU PCI address
RUNTIME_STATUS_PATH = "/sys/bus/pci/devices/0000:01:00.0/power/runtime_status"
COMMAND_TITLE = "GPU Processes"
PROCESS_POLL_MS = 2000 # 5 seconds (for nvidia-smi process list)
STATUS_POLL_MS = 1000   # 1 second (for runtime_status and overall GPU status check)

# ICON CONFIGURATION (Using relative paths for custom icons, falling back to standard names)
ICON_ACTIVE = "icons/active.png"
ICON_IDLE = "icons/idle.png"
ICON_SUSPENDED = "icons/suspended.png"
ICON_ERROR = "dialog-error"
# ---------------------

class SystemTrayApp(QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)

        # --- STATE MANAGEMENT ---
        self.state = "UNKNOWN"
        self.last_parsed_data = None
        self.gpu_status_data = {"utilization": "N/A", "memory_used": "N/A"}
        self.icon_provider = QFileIconProvider()

        self.set_icon(ICON_SUSPENDED)
        self.setToolTip("GPU Monitor: Initializing...")

        # --- TIMERS ---
        # Timer for polling the process list (less frequent)
        self.process_timer = QTimer(self)
        self.process_timer.timeout.connect(self.run_nvidia_smi_processes)

        # Timer for polling overall status and runtime_status file (frequent)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.check_status_and_metrics)

        # --- MENU SETUP ---
        self.menu = QMenu()

        # Action to display GPU metrics
        self.metrics_action = QAction("Utilization: N/A | Memory: N/A", self)
        self.metrics_action.setEnabled(False)
        self.menu.addAction(self.metrics_action)
        self.menu.addSeparator()

        self.quit_action = QAction(QIcon.fromTheme("application-exit"), "Quit", self)
        self.quit_action.triggered.connect(QApplication.instance().quit)
        self.menu.addAction(self.quit_action) # Add quit action to menu initially

        self.setContextMenu(self.menu)

        self.menu.aboutToShow.connect(self.update_process_menu)

        # --- STARTUP LOGIC ---
        self.check_status_and_metrics(startup_check=True)
        self.status_timer.start(STATUS_POLL_MS)

    def set_icon(self, icon_identifier):
        """
        Helper to set the icon. Attempts to load from file path first,
        then falls back to loading from the system theme.
        """
        icon_path = icon_identifier

        # 1. Try loading as a file path
        if os.path.exists(icon_path):
            self.setIcon(QIcon(icon_path))
        else:
            # 2. Fall back to loading as a theme name
            self.setIcon(QIcon.fromTheme(icon_identifier))

    def update_tooltip(self):
        """Updates the tooltip with current state and metrics."""
        # Display "IDLE" instead of "IDLE_DETECTING" in the tooltip
        display_state = self.state if self.state != "IDLE_DETECTING" else "IDLE"

        util = self.gpu_status_data["utilization"]
        mem = self.gpu_status_data["memory_used"]
        tooltip = f"GPU: {display_state}\nUtilization: {util}\nMemory: {mem}"
        self.setToolTip(tooltip)

    def parse_nvidia_smi_output_processes(self, raw_output):
        """Parses the CSV output for processes (PID, Name)."""
        data = []
        for line in raw_output.strip().split('\n'):
            if line:
                try:
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) == 2:
                        data.append(parts)
                except Exception:
                    continue
        return data

    def parse_nvidia_smi_output_status(self, raw_output):
        """Parses the CSV output for status (Utilization, Memory)."""
        data = {"utilization": "N/A", "memory_used": "N/A"}
        lines = raw_output.strip().split('\n')
        if lines:
            try:
                parts = [p.strip() for p in lines[0].split(',')]
                if len(parts) == 2:
                    data["utilization"] = parts[0]
                    data["memory_used"] = parts[1]
            except Exception:
                pass
        return data

    def set_state(self, new_state):
        """Centralized state machine update and icon switching."""
        if self.state != new_state:
            self.state = new_state

            # --- UPDATED ICON LOGIC ---
            if new_state == "SUSPENDED":
                self.set_icon(ICON_SUSPENDED)
            elif new_state == "IDLE_DETECTING":
                self.set_icon(ICON_IDLE)
            elif new_state == "ERROR":
                self.set_icon(ICON_ERROR)
            else: # ACTIVE
                self.set_icon(ICON_ACTIVE)

            print(f"State changed to: {new_state}")

        self.update_tooltip()

    def run_nvidia_smi_processes(self):
        """Runs nvidia-smi for processes. If no processes, switches to suspend detection."""
        # Note: This is only called when self.state is "ACTIVE"
        if self.state != "ACTIVE":
            return

        try:
            result = subprocess.run(
                NVIDIA_SMI_PROCESS_COMMAND,
                capture_output=True,
                text=True,
                check=True,
                encoding='utf-8',
                timeout=3
            )
            parsed_data = self.parse_nvidia_smi_output_processes(result.stdout)

            if not parsed_data:
                self.process_timer.stop()
                self.set_state("IDLE_DETECTING")

            self.last_parsed_data = parsed_data

        except Exception:
            self.last_parsed_data = [["Error", "Command Failed"]]
            self.set_state("ERROR")

    def run_nvidia_smi_status(self):
        """Runs nvidia-smi to get overall GPU status."""
        # Note: This is only called when self.state is "ACTIVE"
        try:
            result = subprocess.run(
                NVIDIA_SMI_STATUS_COMMAND,
                capture_output=True,
                text=True,
                check=True,
                encoding='utf-8',
                timeout=1
            )
            self.gpu_status_data = self.parse_nvidia_smi_output_status(result.stdout)

            # --- CENTERING (ACTIVE) ---
            text = f"Utilization: {self.gpu_status_data['utilization']} | Memory: {self.gpu_status_data['memory_used']}"
            self.metrics_action.setText(text.center(60))
            # --------------------------

        except Exception:
            self.gpu_status_data = {"utilization": "N/A", "memory_used": "N/A"}
            # --- CENTERING (ERROR) ---
            self.metrics_action.setText("Utilization: N/A | Memory: N/A".center(60))
            # -------------------------
            if self.state == "ACTIVE":
                 print("Warning: GPU status check failed.")

        self.update_tooltip()

    def check_runtime_status(self, startup_check=False):
        """Polls the runtime_status file to detect suspend/active events."""
        try:
            with open(RUNTIME_STATUS_PATH, 'r') as f:
                status = f.read().strip()

            is_active = (status == "active")
            is_suspended = (status == "suspended")

            # --- Initial Startup Check Logic ---
            if startup_check or self.state == "UNKNOWN":
                if is_suspended:
                    self.set_state("SUSPENDED")
                elif is_active:
                    self.set_state("ACTIVE")
                    self.process_timer.start(PROCESS_POLL_MS)
                return

            # --- Ongoing Polling Logic (For IDLE/SUSPENDED states) ---

            if self.state == "IDLE_DETECTING":
                if is_suspended:
                    self.process_timer.stop() # Stop the process check when suspended
                    self.set_state("SUSPENDED")

            elif self.state == "SUSPENDED":
                if is_active:
                    self.set_state("ACTIVE")
                    self.process_timer.start(PROCESS_POLL_MS)

        except FileNotFoundError:
            if self.state not in ["ERROR"]:
                 self.status_timer.stop()
                 self.process_timer.stop()
                 self.set_state("ERROR")
        except Exception:
            pass

    def check_status_and_metrics(self, startup_check=False):
        """
        Combined function for status file check and metrics polling.
        Crucially, run_nvidia_smi_status is only called when state is ACTIVE.
        """
        # 1. ALWAYS check runtime status first to correctly set self.state
        self.check_runtime_status(startup_check)

        # 2. ONLY run the frequent nvidia-smi status query if the GPU is confirmed ACTIVE.
        if self.state == "ACTIVE":
            self.run_nvidia_smi_status()
        else:
            # When SUSPENDED, IDLE_DETECTING, or ERROR, we manually set metrics.
            display_text = "IDLE" if self.state == "IDLE_DETECTING" else self.state

            if self.state == "SUSPENDED" or self.state == "IDLE_DETECTING":
                self.gpu_status_data = {"utilization": "0%", "memory_used": "0MiB"}

                # --- CENTERING (IDLE/SUSPENDED) ---
                text = f"Utilization: 0% | Memory: 0MiB"
                self.metrics_action.setText(text.center(60))
                # ----------------------------------

            else: # ERROR
                self.gpu_status_data = {"utilization": "N/A", "memory_used": "N/A"}

                # --- CENTERING (ERROR) ---
                self.metrics_action.setText("Utilization: N/A | Memory: N/A".center(60))
                # -------------------------

            self.update_tooltip()


    def get_flatpak_app_id(self, pid):
        """Uses 'flatpak ps' to find the App ID for a given PID."""
        try:
            result = subprocess.run(
                ["flatpak", "ps", "--columns=pid,application"],
                capture_output=True,
                text=True,
                check=False,
                encoding='utf-8',
                timeout=1
            )

            if result.returncode != 0:
                return None

            for line in result.stdout.split('\n'):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == str(pid):
                    return parts[1]

            return None

        except FileNotFoundError:
            return None
        except Exception:
            return None

    def get_icon_for_process(self, pid, process_name):
        """
        Attempts to find the icon using four strategies: Flatpak, /proc, Process Name, then Fallback.
        """
        # 1. FLATPAK CHECK
        flatpak_app_id = self.get_flatpak_app_id(pid)
        if flatpak_app_id:
            icon = QIcon.fromTheme(flatpak_app_id)
            if not icon.isNull():
                return icon

        # 2. NATIVE /PROC CHECK
        try:
            exe_path = os.readlink(f"/proc/{pid}/exe")
            file_info = QFileInfo(exe_path)

            icon = self.icon_provider.icon(file_info)
            if not icon.isNull():
                return icon

        except FileNotFoundError:
            pass
        except Exception:
            pass

        # 3. PROCESS NAME CHECK
        base_name = process_name.strip().split('.')[0]
        icon = QIcon.fromTheme(base_name)
        if not icon.isNull():
            return icon

        # 4. FINAL FALLBACK
        return QIcon.fromTheme("application-x-executable")

    def on_icon_activated(self, reason):
        """
        Handles activation events. (Right-click is handled by setContextMenu).
        """
        pass

    def get_menu_data(self):
        """Determines the data/message to show in the menu based on the current state."""
        if self.state == "ACTIVE":
            # Call process check on menu open to get the latest list before display
            self.run_nvidia_smi_processes()
            return self.last_parsed_data
        elif self.state == "SUSPENDED":
            return [["Info", "GPU Suspended. No processes."]]
        elif self.state == "IDLE_DETECTING":
            # Display "IDLE" instead of "IDLE_DETECTING"
            return [["Info", "Idle. Monitoring for suspend/activity."]]
        elif self.state == "ERROR":
            return [["Error", "Check path/permissions or command"]]
        else:
            return [["Info", f"State: {self.state}"]]

    def update_process_menu(self):
        """
        Dynamically populates the QMenu with process actions or status messages.
        Fixes the ValueError by robustly clearing and rebuilding the dynamic section.
        """

        # 1. Clear all dynamic actions (everything after the metrics action up to the quit action)
        actions_to_remove = []
        in_dynamic_section = False

        for action in self.menu.actions():
            # Start removing after the metrics line
            if action is self.metrics_action:
                # The separator after metrics also needs to be removed, so we start here.
                # However, the quit action is always at the end.
                in_dynamic_section = True
                continue

            # Stop removing when we hit the quit action
            if action is self.quit_action:
                in_dynamic_section = False
                continue

            if in_dynamic_section:
                actions_to_remove.append(action)

        for action in actions_to_remove:
            self.menu.removeAction(action)

        # 2. Update Header/Metrics Text
        data = self.get_menu_data()

        # 3. Insert new dynamic content (Processes list or status message)

        if data and data != [["Error", "Command Failed"]]:
            # Insert a separator before the process list
            self.menu.insertSeparator(self.quit_action)

            # Insert processes in reverse order so they appear correctly
            for pid_str, process_name in reversed(data):
                try:
                    pid = int(pid_str.strip())
                    icon = self.get_icon_for_process(pid, process_name)
                    process_label = f"[{pid_str.strip()}] {process_name.strip()}"
                    action = QAction(icon, process_label, self)
                    action.setEnabled(False)
                    self.menu.insertAction(self.quit_action, action)

                except ValueError:
                    action = QAction(QIcon.fromTheme("dialog-warning"), f"{process_name.strip()}", self)
                    action.setEnabled(False)
                    self.menu.insertAction(self.quit_action, action)

            # Insert a separator after the metrics and before the process list
            # We insert a separator after the metrics action.
            self.menu.insertSeparator(self.menu.actions()[self.menu.actions().index(self.metrics_action) + 1])


        else:
            # Insert status message if no processes
            if data:
                # Access data[0][1] only if data is not empty
                status_action = QAction(data[0][1], self)
            else:
                # Fallback in case get_menu_data unexpectedly returns []
                status_action = QAction("Status Data Unavailable", self)

            status_action.setEnabled(False)
            self.menu.insertAction(self.quit_action, status_action)
            self.menu.insertSeparator(self.quit_action) # Separator before quit

def main():
    app = QApplication(sys.argv)

    main_window = QMainWindow()
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Systray Error", "No system tray detected.")
        sys.exit(1)

    tray_icon = SystemTrayApp(main_window)
    tray_icon.show()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()
