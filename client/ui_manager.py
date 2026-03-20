import sys
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QTextEdit, QHBoxLayout, QScrollArea
from PyQt5.QtCore import Qt, QPoint, QSize, pyqtSignal, QTimer, QRect
from PyQt5.QtGui import QFont, QIcon, QPainter, QColor, QRegion

class SidebarUI(QWidget):
    # Signals for thread-safe UI updates
    update_chat_signal = pyqtSignal(str, str, bool)   # role, text, update
    update_status_signal = pyqtSignal(str)
    def __init__(self, on_connect_callback, on_mic_callback):
        super().__init__()
        self.on_connect_callback = on_connect_callback
        self.on_mic_callback = on_mic_callback
        self.is_expanded = False
        self.is_mic_on = False
        
        # Connect signals to slots
        self.update_chat_signal.connect(self._add_chat_message_internal)
        self.update_status_signal.connect(self._update_status_internal)
        
        # Setup auto-fade timer
        self.fade_timer = QTimer(self)
        self.fade_timer.setSingleShot(True)
        self.fade_timer.timeout.connect(self._clear_chat)
        
        self.init_ui()

    def init_ui(self):
        try:
            from PyQt5.QtGui import QGuiApplication
            # Window settings (stay on top, frameless)
            self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground)

            self.main_layout = QHBoxLayout()
            self.main_layout.setContentsMargins(0, 0, 0, 0)
            self.main_layout.setSpacing(0)

            # --- Toggle Button Area (Left side of the widget) ---
            self.toggle_area = QWidget()
            self.toggle_area.setFixedWidth(60)
            toggle_layout = QVBoxLayout()
            toggle_layout.setAlignment(Qt.AlignTop)
            
            self.toggle_btn = QPushButton("🔴")
            self.toggle_btn.setFixedSize(50, 50)
            self.toggle_btn.setStyleSheet("""
                QPushButton { border-radius: 25px; background-color: #333; color: white; font-size: 20px; }
                QPushButton:hover { background-color: #555; }
            """)
            self.toggle_btn.clicked.connect(self.toggle_sidebar)
            toggle_layout.addWidget(self.toggle_btn)
            self.toggle_area.setLayout(toggle_layout)
            self.main_layout.addWidget(self.toggle_area)

            # --- Sidebar Area (Right side, expands out) ---
            self.sidebar_area = QWidget()
            # V10.2: Fixed width container with a subtle background to prevent bleed
            self.sidebar_area.setFixedWidth(300) 
            self.sidebar_area.setStyleSheet("background-color: rgba(20, 20, 20, 220); border-left: 1px solid #444; border-radius: 10px 0 0 10px;")
            self.sidebar_area.hide() # Hidden initially
            
            sidebar_layout = QVBoxLayout()
            sidebar_layout.setContentsMargins(15, 20, 15, 20)

            # Header
            self.header = QLabel("Gemini Live Agent")
            self.header.setStyleSheet("color: white; font-size: 16px; font-weight: bold; background: transparent;")
            sidebar_layout.addWidget(self.header)

            # Status
            self.status_label = QLabel("Status: Idle")
            self.status_label.setStyleSheet("color: #AAA; background: transparent;")
            sidebar_layout.addWidget(self.status_label)

            # Connect Button
            self.connect_btn = QPushButton("Connect Backend")
            self.connect_btn.setStyleSheet("background-color: #4285F4; color: white; padding: 8px; border-radius: 5px;")
            self.connect_btn.clicked.connect(self.on_connect_callback)
            sidebar_layout.addWidget(self.connect_btn)

            # Chat History
            self.chat_box = QTextEdit()
            self.chat_box.setReadOnly(True)
            self.chat_box.setStyleSheet("""
                QTextEdit { background-color: rgba(30,30,30,180); color: #EEE; border: 1px solid #555; border-radius: 5px; padding: 5px; }
            """)
            sidebar_layout.addWidget(self.chat_box)

            # Mic Area
            mic_layout = QHBoxLayout()
            self.mic_btn = QPushButton("🎤 (Off)")
            self.mic_btn.setStyleSheet("background-color: #C62828; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
            self.mic_btn.clicked.connect(self.toggle_mic)
            mic_layout.addWidget(self.mic_btn)
            sidebar_layout.addLayout(mic_layout)

            self.sidebar_area.setLayout(sidebar_layout)
            self.main_layout.addWidget(self.sidebar_area)

            self.setLayout(self.main_layout)

            # Positioning: Top Right, collapsed state
            self.screen_geometry = QGuiApplication.primaryScreen().geometry()
            self.set_collapsed_geometry()
            self.update_mask()

        except Exception as e:
            print(f"Failed to initialize UI: {e}")

    def set_collapsed_geometry(self):
        # Just the button
        w, h = 70, 70
        x = self.screen_geometry.width() - w - 10
        y = 20 # Anchor to the top
        self.setGeometry(x, y, w, h)

    def set_expanded_geometry(self):
        # V10.2: Use primaryScreen geometry for reliable placement
        from PyQt5.QtGui import QGuiApplication
        self.screen_geometry = QGuiApplication.primaryScreen().geometry()
        
        sidebar_width = 300 
        total_width = sidebar_width + 70 # sidebar + toggle button area
        h = int(self.screen_geometry.height() * 0.8) # 80% screen height
        x = self.screen_geometry.width() - total_width - 10
        y = 20 # from top
        self.setGeometry(x, y, total_width, h)

    def toggle_sidebar(self):
        self.is_expanded = not self.is_expanded
        if self.is_expanded:
            self.sidebar_area.show()
            self.set_expanded_geometry()
            self.toggle_btn.setText("X") # Collapse icon
        else:
            self.sidebar_area.hide()
            self.set_collapsed_geometry()
            self.toggle_btn.setText("🔴") # Expand icon
        self.update_mask()

    def update_mask(self):
        """Updates the click-mask so only active buttons/text are clickable."""
        region = QRegion()
        
        def add_widget(w):
            nonlocal region
            if w.isVisible() and w.width() > 0 and w.height() > 0:
                pos = w.mapTo(self, QPoint(0, 0))
                region = region.united(QRegion(QRect(pos, w.size())))
            return region

        # Always include the toggle button
        region = add_widget(self.toggle_btn)
        
        if self.is_expanded:
            # Include interactive sidebar parts
            region = add_widget(self.header)
            region = add_widget(self.status_label)
            region = add_widget(self.connect_btn)
            region = add_widget(self.mic_btn)
            
            # Only mask the chat box if it actually has content
            if self.chat_box.toPlainText().strip():
                region = add_widget(self.chat_box)
        
        self.setMask(region)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_mask()

    def toggle_mic(self):
        self.is_mic_on = self.on_mic_callback() # call logic and get intent
        if self.is_mic_on:
             self.mic_btn.setText("🎙️ 〰️〰️ (On)")
             self.mic_btn.setStyleSheet("background-color: #2E7D32; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        else:
             self.mic_btn.setText("🎤 (Off)")
             self.mic_btn.setStyleSheet("background-color: #C62828; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")

    def _add_chat_message_internal(self, role, text, update=False):
        colors = {"User": "#8AB4F8", "Agent": "#81C995", "System": "#FF8A65", "System Error": "#FF5252"}
        # Pick color by prefix so emoji variants still match
        color = next((v for k, v in colors.items() if role.startswith(k)), "white")
        html = f'<div style="margin-bottom: 5px;"><b><span style="color: {color};">{role}:</span></b> {text}</div>'

        if update:
            # Replace the last block in the document in-place
            cursor = self.chat_box.textCursor()
            cursor.movePosition(cursor.End)
            cursor.select(cursor.BlockUnderCursor)
            if cursor.selectedText():  # there's a block to replace
                # Move up to select the entire last HTML block
                doc = self.chat_box.document()
                block = doc.lastBlock()
                cursor.setPosition(block.position())
                cursor.movePosition(cursor.End, cursor.KeepAnchor)
                cursor.insertHtml(html)
            else:
                self.chat_box.append(html)
        else:
            self.chat_box.append(html)
        scrollbar = self.chat_box.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        # Restart the 10-second fade timer every time a message is added
        self.fade_timer.start(10000)
        self.update_mask()

    def _clear_chat(self):
        """Clears the chat box when the fade timer expires."""
        self.chat_box.clear()
        self.update_mask()

    def _update_status_internal(self, text):
        self.status_label.setText(f"Status: {text}")

    # Public thread-safe methods to update UI
    def add_chat_message(self, role, text, update=False):
        self.update_chat_signal.emit(role, text, update)
        
    def update_status(self, text):
        self.update_status_signal.emit(text)

    def show_error(self, message):
         self.add_chat_message("System Error", message)

if __name__ == "__main__":
    def dummy_conn(): print("connect")
    def dummy_mic(): return True
    app = QApplication(sys.argv)
    ui = SidebarUI(dummy_conn, dummy_mic)
    ui.show()
    sys.exit(app.exec_())
