import sys
import subprocess
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, 
                             QPushButton, QHBoxLayout, QHeaderView, QLabel)
from PyQt6.QtCore import Qt, QEvent

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

SMI_PROCESS_CMD = ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"]

class GpuTaskManager(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NVIDIA GPU Task Manager")
        self.resize(600, 450)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        
        self.layout = QVBoxLayout(self)
        self.label = QLabel("Active GPU Processes")
        self.label.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        self.layout.addWidget(self.label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["PID", "Process Name", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_list)
        
        self.kill_btn = QPushButton("Kill Process")
        self.kill_btn.setStyleSheet("background-color: #442222; color: white; font-weight: bold;")
        self.kill_btn.clicked.connect(self.kill_selected)
        
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addWidget(self.kill_btn)
        self.layout.addLayout(btn_layout)
        
        self.refresh_list()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                self.close()
        super().changeEvent(event)

    def fetch_all_processes(self):
        combined = {}
        try:
            res = subprocess.run(SMI_PROCESS_CMD, capture_output=True, text=True, timeout=2)
            for line in res.stdout.strip().split('\n'):
                if line and ',' in line:
                    pid, name = line.split(',', 1)
                    combined[pid.strip()] = [name.strip(), "nvidia-smi"]
        except: pass

        try:
            res = subprocess.run(PROC_QUERY_CMD, shell=True, capture_output=True, text=True, timeout=2)
            for line in res.stdout.strip().split('\n'):
                if line:
                    parts = line.split(' ', 1)
                    if len(parts) == 2:
                        pid, name = parts
                        if pid not in combined:
                            combined[pid] = [name, "/proc"]
        except: pass
        return combined

    def refresh_list(self):
        processes = self.fetch_all_processes()
        self.table.setRowCount(0)
        for pid, (name, source) in processes.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(pid))
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(source))

    def kill_selected(self):
        item = self.table.currentItem()
        if item:
            pid = self.table.item(self.table.row(item), 0).text()
            if pid and pid.isdigit():
                subprocess.run(["kill", "-9", pid])
                self.refresh_list()