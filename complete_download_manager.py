#!/usr/bin/env python3
"""
Complete Internet Download Manager (IDM) in Python
Cross-platform GUI application with multi-threaded downloads
"""
import os
import sys
import time
import threading
import requests
import urllib.parse
import ftplib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

class DownloadStatus(Enum):
    PENDING = "Pending"
    DOWNLOADING = "Downloading"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"

@dataclass
class DownloadSegment:
    start_byte: int
    end_byte: int
    downloaded_bytes: int = 0
    is_complete: bool = False

class DownloadWorker(QThread):
    """Worker thread for handling downloads"""
    progress_updated = pyqtSignal(str, int, int, float, str)  # id, downloaded, total, speed, status
    download_completed = pyqtSignal(str, str)  # id, status
    
    def __init__(self, download_id, url, destination, segments=4):
        super().__init__()
        self.download_id = download_id
        self.url = url
        self.destination = destination
        self.segments_count = segments
        self.is_paused = False
        self.is_cancelled = False
        self.segments = []
        self.total_size = 0
        self.downloaded_size = 0
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Python Download Manager 1.0'
        })
        
    def pause(self):
        """Pause the download"""
        self.is_paused = True
        
    def resume(self):
        """Resume the download"""
        self.is_paused = False
        
    def cancel(self):
        """Cancel the download"""
        self.is_cancelled = True
        
    def get_file_info(self):
        """Get file size and check if range requests are supported"""
        try:
            if self.url.startswith('ftp://'):
                return self._get_ftp_info()
            else:
                response = self.session.head(self.url, timeout=30)
                if response.status_code == 405:  # HEAD not allowed
                    response = self.session.get(self.url, stream=True, timeout=30)
                    response.close()
                
                content_length = response.headers.get('Content-Length')
                if content_length:
                    self.total_size = int(content_length)
                
                supports_range = response.headers.get('Accept-Ranges') == 'bytes'
                return supports_range
        except Exception as e:
            print(f"Error getting file info: {e}")
            return False
            
    def _get_ftp_info(self):
        """Get FTP file information"""
        try:
            parsed = urllib.parse.urlparse(self.url)
            ftp = ftplib.FTP()
            ftp.connect(parsed.hostname, parsed.port or 21)
            if parsed.username:
                ftp.login(parsed.username, parsed.password or '')
            else:
                ftp.login()
            
            self.total_size = ftp.size(parsed.path) or 0
            ftp.quit()
            return True
        except:
            return False
            
    def create_segments(self):
        """Create download segments for multi-part downloading"""
        if self.total_size <= 0:
            self.segments = [DownloadSegment(0, 0)]
            return
            
        segment_size = self.total_size // self.segments_count
        for i in range(self.segments_count):
            start = i * segment_size
            end = start + segment_size - 1 if i < self.segments_count - 1 else self.total_size - 1
            self.segments.append(DownloadSegment(start, end))
            
    def download_segment(self, segment, temp_file):
        """Download a specific segment"""
        try:
            headers = {'Range': f'bytes={segment.start_byte}-{segment.end_byte}'}
            response = self.session.get(self.url, headers=headers, stream=True, timeout=30)
            
            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if self.is_cancelled:
                        return
                    while self.is_paused and not self.is_cancelled:
                        time.sleep(0.1)
                    
                    if chunk:
                        f.write(chunk)
                        segment.downloaded_bytes += len(chunk)
                        
            segment.is_complete = True
        except Exception as e:
            print(f"Segment download error: {e}")
            
    def run(self):
        """Main download execution"""
        try:
            supports_range = self.get_file_info()
            
            if not supports_range or self.total_size <= 1024*1024:  # Single thread for small files
                self._download_single()
            else:
                self._download_multi()
                
        except Exception as e:
            self.download_completed.emit(self.download_id, DownloadStatus.FAILED.value)
            
    def _download_single(self):
        """Single-threaded download"""
        try:
            response = self.session.get(self.url, stream=True)
            start_time = time.time()
            
            with open(self.destination, 'wb') as f:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if self.is_cancelled:
                        return
                    while self.is_paused and not self.is_cancelled:
                        time.sleep(0.1)
                        
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # Calculate speed and emit progress
                        elapsed = time.time() - start_time
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        self.progress_updated.emit(
                            self.download_id, downloaded, self.total_size or downloaded,
                            speed, DownloadStatus.DOWNLOADING.value
                        )
                        
            if not self.is_cancelled:
                self.download_completed.emit(self.download_id, DownloadStatus.COMPLETED.value)
                
        except Exception as e:
            self.download_completed.emit(self.download_id, DownloadStatus.FAILED.value)
            
    def _download_multi(self):
        """Multi-threaded download with segments"""
        try:
            self.create_segments()
            temp_dir = Path(self.destination).parent / f".{Path(self.destination).name}_temp"
            temp_dir.mkdir(exist_ok=True)
            
            # Download segments in parallel
            with ThreadPoolExecutor(max_workers=self.segments_count) as executor:
                futures = []
                for i, segment in enumerate(self.segments):
                    temp_file = temp_dir / f"segment_{i}"
                    future = executor.submit(self.download_segment, segment, temp_file)
                    futures.append(future)
                
                # Monitor progress
                start_time = time.time()
                while not all(f.done() for f in futures) and not self.is_cancelled:
                    total_downloaded = sum(s.downloaded_bytes for s in self.segments)
                    elapsed = time.time() - start_time
                    speed = total_downloaded / elapsed if elapsed > 0 else 0
                    
                    self.progress_updated.emit(
                        self.download_id, total_downloaded, self.total_size,
                        speed, DownloadStatus.DOWNLOADING.value
                    )
                    time.sleep(0.5)
                    
            if not self.is_cancelled and all(s.is_complete for s in self.segments):
                # Combine segments
                with open(self.destination, 'wb') as output:
                    for i in range(len(self.segments)):
                        temp_file = temp_dir / f"segment_{i}"
                        if temp_file.exists():
                            with open(temp_file, 'rb') as input_file:
                                output.write(input_file.read())
                
                # Cleanup
                for temp_file in temp_dir.iterdir():
                    temp_file.unlink()
                temp_dir.rmdir()
                
                self.download_completed.emit(self.download_id, DownloadStatus.COMPLETED.value)
            else:
                self.download_completed.emit(self.download_id, DownloadStatus.FAILED.value)
                
        except Exception as e:
            self.download_completed.emit(self.download_id, DownloadStatus.FAILED.value)

class DownloadItem(QWidget):
    """Widget representing a single download item in the queue"""
    def __init__(self, download_id, url, destination, parent=None):
        super().__init__(parent)
        self.download_id = download_id
        self.url = url
        self.destination = destination
        self.worker = None
        self.status = DownloadStatus.PENDING
        self.retry_count = 0
        self.max_retries = 3
        
        self.setup_ui()
        
    def setup_ui(self):
        """Setup the UI for download item"""
        layout = QVBoxLayout()
        
        # File info
        self.filename_label = QLabel(os.path.basename(self.destination))
        self.filename_label.setStyleSheet("font-weight: bold;")
        
        self.url_label = QLabel(self.url)
        self.url_label.setStyleSheet("color: gray; font-size: 10px;")
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        
        # Status and controls
        controls_layout = QHBoxLayout()
        
        self.status_label = QLabel("Pending")
        self.speed_label = QLabel("0 KB/s")
        self.size_label = QLabel("0 / 0 MB")
        
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_download)
        
        controls_layout.addWidget(self.status_label)
        controls_layout.addWidget(self.speed_label)
        controls_layout.addWidget(self.size_label)
        controls_layout.addStretch()
        controls_layout.addWidget(self.pause_btn)
        controls_layout.addWidget(self.cancel_btn)
        
        layout.addWidget(self.filename_label)
        layout.addWidget(self.url_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(controls_layout)
        
        self.setLayout(layout)
        
    def start_download(self):
        """Start the download"""
        if self.worker and self.worker.isRunning():
            return
            
        self.worker = DownloadWorker(self.download_id, self.url, self.destination)
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.download_completed.connect(self.download_finished)
        self.worker.start()
        
        self.status = DownloadStatus.DOWNLOADING
        self.update_status()
        
    def toggle_pause(self):
        """Toggle pause/resume"""
        if not self.worker:
            return
            
        if self.status == DownloadStatus.DOWNLOADING:
            self.worker.pause()
            self.status = DownloadStatus.PAUSED
            self.pause_btn.setText("Resume")
        elif self.status == DownloadStatus.PAUSED:
            self.worker.resume()
            self.status = DownloadStatus.DOWNLOADING
            self.pause_btn.setText("Pause")
            
        self.update_status()
        
    def cancel_download(self):
        """Cancel the download"""
        if self.worker:
            self.worker.cancel()
        self.status = DownloadStatus.CANCELLED
        self.update_status()
        
    def update_progress(self, download_id, downloaded, total, speed, status):
        """Update progress information"""
        if download_id != self.download_id:
            return
            
        if total > 0:
            progress = int((downloaded / total) * 100)
            self.progress_bar.setValue(progress)
            
        # Format sizes
        downloaded_mb = downloaded / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        speed_kb = speed / 1024
        
        self.size_label.setText(f"{downloaded_mb:.1f} / {total_mb:.1f} MB")
        self.speed_label.setText(f"{speed_kb:.1f} KB/s")
        
    def download_finished(self, download_id, status):
        """Handle download completion"""
        if download_id != self.download_id:
            return
            
        if status == DownloadStatus.FAILED.value and self.retry_count < self.max_retries:
            self.retry_count += 1
            QTimer.singleShot(2000, self.start_download)  # Retry after 2 seconds
            return
            
        self.status = DownloadStatus(status)
        self.update_status()
        
        if self.status == DownloadStatus.COMPLETED:
            self.progress_bar.setValue(100)
            self.pause_btn.setEnabled(False)
            
    def update_status(self):
        """Update status display"""
        self.status_label.setText(self.status.value)
        
        if self.status in [DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED]:
            self.pause_btn.setEnabled(False)
            self.cancel_btn.setEnabled(False)

class AddDownloadDialog(QDialog):
    """Dialog for adding new downloads"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Download")
        self.setModal(True)
        self.resize(500, 200)
        self.setup_ui()
        
    def setup_ui(self):
        """Setup dialog UI"""
        layout = QVBoxLayout()
        
        # URL input
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Enter download URL (HTTP, HTTPS, or FTP)")
        url_layout.addWidget(self.url_edit)
        
        # Destination
        dest_layout = QHBoxLayout()
        dest_layout.addWidget(QLabel("Save to:"))
        self.dest_edit = QLineEdit()
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self.browse_destination)
        dest_layout.addWidget(self.dest_edit)
        dest_layout.addWidget(self.browse_btn)
        
        # Segments
        segments_layout = QHBoxLayout()
        segments_layout.addWidget(QLabel("Segments:"))
        self.segments_spin = QSpinBox()
        self.segments_spin.setMinimum(1)
        self.segments_spin.setMaximum(16)
        self.segments_spin.setValue(4)
        segments_layout.addWidget(self.segments_spin)
        segments_layout.addStretch()
        
        # Buttons
        buttons_layout = QHBoxLayout()
        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.ok_btn)
        buttons_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(url_layout)
        layout.addLayout(dest_layout)
        layout.addLayout(segments_layout)
        layout.addLayout(buttons_layout)
        
        self.setLayout(layout)
        
        # Set default download directory
        downloads_dir = str(Path.home() / "Downloads")
        self.dest_edit.setText(downloads_dir)
        
    def browse_destination(self):
        """Open file browser for destination"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save File", self.dest_edit.text()
        )
        if file_path:
            self.dest_edit.setText(file_path)
            
    def get_download_info(self):
        """Get download information from dialog"""
        return {
            'url': self.url_edit.text().strip(),
            'destination': self.dest_edit.text().strip(),
            'segments': self.segments_spin.value()
        }

class DownloadManager(QMainWindow):
    """Main download manager window"""
    def __init__(self):
        super().__init__()
        self.downloads = {}
        self.download_counter = 0
        self.setup_ui()
        
    def setup_ui(self):
        """Setup main window UI"""
        self.setWindowTitle("Python Download Manager")
        self.setGeometry(100, 100, 800, 600)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        layout = QVBoxLayout()
        
        # Toolbar
        toolbar_layout = QHBoxLayout()
        
        self.add_btn = QPushButton("Add Download")
        self.add_btn.clicked.connect(self.add_download)
        
        self.start_all_btn = QPushButton("Start All")
        self.start_all_btn.clicked.connect(self.start_all_downloads)
        
        self.pause_all_btn = QPushButton("Pause All")
        self.pause_all_btn.clicked.connect(self.pause_all_downloads)
        
        self.clear_completed_btn = QPushButton("Clear Completed")
        self.clear_completed_btn.clicked.connect(self.clear_completed)
        
        toolbar_layout.addWidget(self.add_btn)
        toolbar_layout.addWidget(self.start_all_btn)
        toolbar_layout.addWidget(self.pause_all_btn)
        toolbar_layout.addWidget(self.clear_completed_btn)
        toolbar_layout.addStretch()
        
        # Downloads list
        self.scroll_area = QScrollArea()
        self.downloads_widget = QWidget()
        self.downloads_layout = QVBoxLayout()
        self.downloads_widget.setLayout(self.downloads_layout)
        self.scroll_area.setWidget(self.downloads_widget)
        self.scroll_area.setWidgetResizable(True)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        layout.addLayout(toolbar_layout)
        layout.addWidget(self.scroll_area)
        
        central_widget.setLayout(layout)
        
        # Menu bar
        self.create_menu_bar()
        
    def create_menu_bar(self):
        """Create menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu('File')
        
        add_action = QAction('Add Download', self)
        add_action.setShortcut('Ctrl+N')
        add_action.triggered.connect(self.add_download)
        file_menu.addAction(add_action)
        
        exit_action = QAction('Exit', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
    def add_download(self):
        """Add new download"""
        dialog = AddDownloadDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            info = dialog.get_download_info()
            if info['url'] and info['destination']:
                self.create_download_item(info['url'], info['destination'])
                
    def create_download_item(self, url, destination):
        """Create new download item"""
        self.download_counter += 1
        download_id = f"download_{self.download_counter}"
        
        item = DownloadItem(download_id, url, destination)
        self.downloads[download_id] = item
        
        self.downloads_layout.addWidget(item)
        self.update_status_bar()
        
        # Auto-start download
        item.start_download()
        
    def start_all_downloads(self):
        """Start all pending downloads"""
        for item in self.downloads.values():
            if item.status == DownloadStatus.PENDING:
                item.start_download()
                
    def pause_all_downloads(self):
        """Pause all active downloads"""
        for item in self.downloads.values():
            if item.status == DownloadStatus.DOWNLOADING:
                item.toggle_pause()
                
    def clear_completed(self):
        """Remove completed downloads from list"""
        to_remove = []
        for download_id, item in self.downloads.items():
            if item.status in [DownloadStatus.COMPLETED, DownloadStatus.CANCELLED]:
                to_remove.append(download_id)
                self.downloads_layout.removeWidget(item)
                item.deleteLater()
                
        for download_id in to_remove:
            del self.downloads[download_id]
            
        self.update_status_bar()
        
    def update_status_bar(self):
        """Update status bar with download statistics"""
        total = len(self.downloads)
        downloading = sum(1 for item in self.downloads.values() 
                         if item.status == DownloadStatus.DOWNLOADING)
        completed = sum(1 for item in self.downloads.values() 
                        if item.status == DownloadStatus.COMPLETED)
        
        self.status_bar.showMessage(
            f"Total: {total} | Downloading: {downloading} | Completed: {completed}"
        )

def main():
    """Main application entry point"""
    app = QApplication(sys.argv)
    
    # Set application properties
    app.setApplicationName("Python Download Manager")
    app.setOrganizationName("Python Software")
    
    # Create and show main window
    window = DownloadManager()
    window.show()
    
    # Start event loop
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
