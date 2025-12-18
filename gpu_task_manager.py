import sys
import subprocess
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, 
                             QPushButton, QHBoxLayout, QHeaderView, QLabel)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer, QEvent

# Attempt to import NVML for direct library access
try:
    import pynvml
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False

# High-performance shell pipeline fallback for non-NVML reported processes
PROC_QUERY_CMD = (
    "find /proc/[0-9]*/fd -lname '/dev/nvidia*' 2>/dev/null | "
    "awk -F/ '!seen[$3]++ { "
    "pid = $3; "
    "if ((getline name < (\"/proc/\" pid \"/comm\")) > 0) { "
    "  printf \"%s %s\\n\", pid, name; "
    "  close(\"/proc/\" pid \"/comm\"); "
    "} "
    "}'"
)

class GPUWorker(QObject):
    """Method 2: Background worker to prevent UI freezing during driver queries."""
    data_ready = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._is_initialized = False
        self.handle = None

    def _ensure_init(self):
        if not NVML_AVAILABLE: return False
        if not self._is_initialized:
            try:
                pynvml.nvmlInit()
                # Get handle for the primary GPU
                self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._is_initialized = True
            except: return False
        return True

    def fetch_processes(self):
        """Method 3: Batched query of Compute, Graphics, and System processes."""
        combined = {}
        
        # 1. Try NVML (Direct Library Access)
        if self._ensure_init():
            try:
                # Merge both compute and graphics processes
                nv_procs = (pynvml.nvmlDeviceGetComputeRunningProcesses(self.handle) + 
                            pynvml.nvmlDeviceGetGraphicsRunningProcesses(self.handle))
                
                for p in nv_procs:
                    try:
                        name = pynvml.nvmlSystemGetProcessName(p.pid)
                        combined[str(p.pid)] = [name, "NVML"]
                    except: continue
            except: pass

        # 2. Fallback to /proc shell query
        try:
            res = subprocess.run(PROC_QUERY_CMD, shell=True, capture_output=True, text=True, timeout=1)
            for line in res.stdout.strip().split('\n'):
                if line:
                    parts = line.split(' ', 1)
                    if len(parts) == 2:
                        pid, name = parts
                        if pid not in combined:
                            combined[pid] = [name, "/proc"]
        except: pass

        self.data_ready.emit(combined)

class GpuTaskManager(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NVIDIA GPU Task Manager")
        self.resize(650, 450)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        
        # Layout Setup
        self.layout = QVBoxLayout(self)
        self.label = QLabel("Active GPU Processes (Auto-Refreshing)")
        self.label.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        self.layout.addWidget(self.label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["PID", "Process Name", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.layout.addWidget(self.table)

        # Buttons
        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh Now")
        self.refresh_btn.clicked.connect(self.request_refresh)
        
        self.kill_btn = QPushButton("Kill Process")
        self.kill_btn.setStyleSheet("background-color: #442222; color: white; font-weight: bold;")
        self.kill_btn.clicked.connect(self.kill_normal)
        
        self.aggressive_kill_btn = QPushButton("Force Kill Process")
        self.aggressive_kill_btn.setStyleSheet("background-color: #880000; color: white; font-weight: bold;")
        self.aggressive_kill_btn.clicked.connect(self.kill_aggressive)
        
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.kill_btn)
        btn_layout.addWidget(self.aggressive_kill_btn)
        self.layout.addLayout(btn_layout)

        # Threaded Worker Setup
        self.worker_thread = QThread()
        self.worker = GPUWorker()
        self.worker.moveToThread(self.worker_thread)
        self.worker.data_ready.connect(self.populate_table)
        self.worker_thread.start()

        # Auto-Refresh Timer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.request_refresh)

    def showEvent(self, event):
        """Starts auto-refresh when window is shown."""
        super().showEvent(event)
        self.refresh_timer.start(2000) # 2 seconds
        self.request_refresh()

    def hideEvent(self, event):
        """Stops auto-refresh when hidden."""
        super().hideEvent(event)
        self.refresh_timer.stop()

    def changeEvent(self, event):
        """Auto-close when the window loses focus."""
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                self.close()
        super().changeEvent(event)

    def request_refresh(self):
        """Signals background worker."""
        self.worker.fetch_processes()

    def populate_table(self, processes):
        selected_pid = None
        current_row = self.table.currentRow()
        if current_row != -1:
            selected_pid = self.table.item(current_row, 0).text()

        self.table.setRowCount(0)
        for pid, (name, source) in processes.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(pid))
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(source))
            if pid == selected_pid:
                self.table.selectRow(row)

    def kill_normal(self):
        item = self.table.currentItem()
        if item:
            pid = self.table.item(self.table.row(item), 0).text()
            if pid and pid.isdigit():
                subprocess.run(["kill", "-9", pid])
                self.request_refresh()

    def kill_aggressive(self):
        item = self.table.currentItem()
        if item:
            row = self.table.row(item)
            pid = self.table.item(row, 0).text()
            name = self.table.item(row, 1).text()
            if pid and pid.isdigit():
                subprocess.run(["kill", "-9", pid])
                clean_name = name.split('/')[-1]
                subprocess.run(["pkill", "-9", "-f", clean_name])
                self.request_refresh()

if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    window = GpuTaskManager()
    window.show()
    sys.exit(app.exec())
