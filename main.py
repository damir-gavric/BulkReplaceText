import os
import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QCheckBox,
    QTabWidget, QSplitter, QListWidget, QTextEdit, QFileDialog,
    QMessageBox, QListWidgetItem, QSizePolicy
)
from PyQt6.QtGui import QPalette, QColor, QDesktopServices, QTextCursor
from PyQt6.QtCore import Qt, QUrl

import html


# -----------------------------
# File helpers
# -----------------------------

def is_probably_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    weird = sum(1 for b in data if b < 9 or (13 < b < 32))
    return weird > 0 and (weird / max(1, len(data))) > 0.02


def read_text_file(path: str):
    with open(path, "rb") as f:
        raw = f.read()

    if is_probably_binary(raw):
        return None

    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue

    return None


def write_text_file(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def parse_extensions(csv: str) -> set[str]:
    exts = set()
    for part in (csv or "").split(","):
        e = part.strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        exts.add(e)
    return exts


# -----------------------------
# Replacement map helpers
# -----------------------------

def load_replacements_map(map_path: str):
    """
    File format:
      old;new
    Skips empty lines and lines starting with #.
    Returns dict old->new
    """
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception as e:
        return None, f"Ne mogu da pročitam map file:\n{e}"

    repl = {}
    warnings = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ";" not in line:
            warnings.append(f"Preskačem liniju (nema ';'): {line}")
            continue

        old, new = line.split(";", 1)
        old, new = old.strip(), new.strip()

        if not old:
            warnings.append(f"Preskačem liniju (prazan 'stari' string): {line}")
            continue

        repl[old] = new

    return repl, warnings


def check_duplicate_new_names(replacements: dict):
    seen = {}
    duplicates = {}
    for old, new in replacements.items():
        if new in seen:
            if new not in duplicates:
                duplicates[new] = [seen[new]]
            duplicates[new].append(old)
        else:
            seen[new] = old
    return duplicates


# -----------------------------
# Core scan + preview
# -----------------------------

def scan_files(folder: str, include_subfolders: bool, exts: set[str]) -> list[str]:
    paths = []
    if include_subfolders:
        for root, _, files in os.walk(folder):
            for fn in files:
                p = os.path.join(root, fn)
                if not os.path.isfile(p):
                    continue
                if exts:
                    _, ext = os.path.splitext(fn.lower())
                    if ext not in exts:
                        continue
                paths.append(p)
    else:
        for fn in os.listdir(folder):
            p = os.path.join(folder, fn)
            if not os.path.isfile(p):
                continue
            if exts:
                _, ext = os.path.splitext(fn.lower())
                if ext not in exts:
                    continue
            paths.append(p)

    paths.sort()
    return paths


def build_preview_single(text: str, old: str, new: str, max_examples: int = 5, context: int = 60):
    examples = []
    if not old:
        return examples

    start = 0
    for _ in range(max_examples):
        idx = text.find(old, start)
        if idx == -1:
            break

        left = max(0, idx - context)
        right = min(len(text), idx + len(old) + context)

        # Grab raw strings
        before_raw = text[left:right]
        # Replace the first instance of 'old' in the context slice
        after_raw = before_raw.replace(old, new, 1)

        # Convert to HTML format for rendering
        # Escape HTML so actual tags in the source text aren't rendered as layout
        b_html = html.escape(before_raw)
        a_html = html.escape(after_raw)

        # Wrap the target tokens in bold tags (escape the tokens themselves first to match)
        b_html = b_html.replace(html.escape(old), f"<b><font color='#e07b00'>{html.escape(old)}</font></b>", 1)
        a_html = a_html.replace(html.escape(new), f"<b><font color='#2e9e4f'>{html.escape(new)}</font></b>", 1)

        # Preserve whitespace visually
        b_html = b_html.replace("\n", "<br>").replace(" ", "&nbsp;")
        a_html = a_html.replace("\n", "<br>").replace(" ", "&nbsp;")

        examples.append((b_html, a_html))
        start = idx + len(old)

    return examples


def build_preview_map(text: str, replacements: dict, max_examples: int = 6, context: int = 60):
    """
    Shows examples across multiple pairs.
    Returns list of (label, before, after) up to max_examples total.
    """
    out = []
    if not replacements:
        return out

    # deterministic order: longest 'old' first (reduces "small token" noise in preview)
    items = sorted(replacements.items(), key=lambda kv: len(kv[0]), reverse=True)

    for old, new in items:
        if len(out) >= max_examples:
            break
        start = 0
        found_any = False

        # find first match only for each pair (preview should be short)
        idx = text.find(old, start)
        if idx != -1:
            found_any = True
            left = max(0, idx - context)
            right = min(len(text), idx + len(old) + context)

            before_raw = text[left:right]
            after_raw = before_raw.replace(old, new, 1)

            b_html = html.escape(before_raw)
            a_html = html.escape(after_raw)

            b_html = b_html.replace(html.escape(old), f"<b><font color='#e07b00'>{html.escape(old)}</font></b>", 1)
            a_html = a_html.replace(html.escape(new), f"<b><font color='#2e9e4f'>{html.escape(new)}</font></b>", 1)

            b_html = b_html.replace("\n", "<br>").replace(" ", "&nbsp;")
            a_html = a_html.replace("\n", "<br>").replace(" ", "&nbsp;")

            out.append((f"{html.escape(old)}  &rarr;  {html.escape(new)}", b_html, a_html))

        if found_any and len(out) >= max_examples:
            break

    return out


def apply_replacements(text: str, mode: str, old: str, new: str, replacements_map: dict):
    """
    Returns (new_text, total_replacements_count, per_pair_counts_dict)
    """
    if mode == "single":
        if not old:
            return text, 0, {}
        c = text.count(old)
        return text.replace(old, new), c, {f"{old} → {new}": c}

    # mode == "map"
    if not replacements_map:
        return text, 0, {}

    per = {}
    # apply in deterministic order (longest first)
    items = sorted(replacements_map.items(), key=lambda kv: len(kv[0]), reverse=True)
    new_text = text
    total = 0
    for o, n in items:
        c = new_text.count(o)
        if c:
            new_text = new_text.replace(o, n)
            per[f"{o} → {n}"] = c
            total += c
    return new_text, total, per


# -----------------------------
# GUI app
# -----------------------------

class BulkReplaceApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bulk Replace with Preview (Single + Map)")
        self.resize(1200, 700)

        # Enable dropping files/folders directly onto the window
        self.setAcceptDrops(True)

        self.source_folder = ""
        self.file_paths: list[str] = []
        self.map_file_path = ""
        self.replacements_map = {}

        # Main central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # --- Top Controls ---
        top_layout = QGridLayout()

        top_layout.addWidget(QLabel("Folder:"), 0, 0)
        self.folder_label = QLabel("(not selected)")
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top_layout.addWidget(self.folder_label, 0, 1)

        btn_sel_folder = QPushButton("Select folder...")
        btn_sel_folder.clicked.connect(self.on_select_folder)
        top_layout.addWidget(btn_sel_folder, 0, 2)

        top_layout.addWidget(QLabel("Extensions:"), 1, 0)
        self.ext_entry = QLineEdit()
        self.ext_entry.setText(".txt,.md,.html,.xhtml,.css,.sql,.ddl,.dml,.psql,.mysql,.plsql,.tsql,.prc,.fnc,.vw")
        top_layout.addWidget(self.ext_entry, 1, 1, 1, 2)

        self.include_sub = QCheckBox("Include subfolders")
        self.include_sub.setChecked(True)
        top_layout.addWidget(self.include_sub, 2, 1)

        # Theme toggle
        self.theme_checkbox = QCheckBox("Dark Theme")
        self.theme_checkbox.setChecked(False)  # Light by default initially
        self.theme_checkbox.toggled.connect(self.toggle_theme)
        top_layout.addWidget(self.theme_checkbox, 2, 2, alignment=Qt.AlignmentFlag.AlignRight)

        main_layout.addLayout(top_layout)

        # --- Settings Area (Tabs) ---
        self.tabs = QTabWidget()

        # Single Tab
        self.single_tab = QWidget()
        single_layout = QGridLayout(self.single_tab)
        single_layout.addWidget(QLabel("Text to replace:"), 0, 0)
        self.old_entry = QLineEdit()
        self.old_entry.textChanged.connect(self.on_file_select)
        single_layout.addWidget(self.old_entry, 0, 1)

        single_layout.addWidget(QLabel("Replace with:"), 1, 0)
        self.new_entry = QLineEdit()
        self.new_entry.textChanged.connect(self.on_file_select)
        single_layout.addWidget(self.new_entry, 1, 1)
        self.tabs.addTab(self.single_tab, "Single replace")

        # Map Tab
        self.map_tab = QWidget()
        map_layout = QHBoxLayout(self.map_tab)
        self.map_label = QLabel("Map file: (not selected)")
        map_layout.addWidget(self.map_label)
        btn_sel_map = QPushButton("Select map file...")
        btn_sel_map.clicked.connect(self.on_select_map)
        map_layout.addWidget(btn_sel_map)
        self.tabs.addTab(self.map_tab, "Map file (old;new)")

        self.tabs.currentChanged.connect(self.on_mode_change)
        main_layout.addWidget(self.tabs)

        # --- Action Bar ---
        action_layout = QHBoxLayout()
        btn_rescan = QPushButton("Rescan files")
        btn_rescan.clicked.connect(self.on_rescan)
        action_layout.addWidget(btn_rescan)

        action_layout.addStretch()

        btn_run = QPushButton("Run Replace (selected files)")
        btn_run.clicked.connect(self.on_run)
        # Style the run button
        btn_run.setStyleSheet("font-weight: bold;")
        action_layout.addWidget(btn_run)

        main_layout.addLayout(action_layout)

        # --- Main Splitter ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter, 1)  # Give it stretch = 1

        # Left Pane: File List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("Files (selected by default):"))

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search files...")
        self.search_input.textChanged.connect(self.on_search_changed)
        left_layout.addWidget(self.search_input)

        self.file_list = QListWidget()
        self.file_list.itemSelectionChanged.connect(self.on_file_select)
        self.file_list.itemDoubleClicked.connect(self.on_file_double_click)
        left_layout.addWidget(self.file_list)

        btns_layout = QHBoxLayout()
        btn_sel_all = QPushButton("Select all")
        btn_sel_all.clicked.connect(self.select_all)
        btns_layout.addWidget(btn_sel_all)

        btn_sel_none = QPushButton("Select none")
        btn_sel_none.clicked.connect(self.select_none)
        btns_layout.addWidget(btn_sel_none)
        left_layout.addLayout(btns_layout)

        self.stats_label = QLabel("0 files")
        left_layout.addWidget(self.stats_label)

        self.main_splitter.addWidget(left_widget)

        # Right Pane: Preview & Log
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        # Preview Area (Top Right)
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)

        preview_layout.addWidget(QLabel("Preview (click a file on the left):"))

        preview_h_splitter = QSplitter(Qt.Orientation.Horizontal)

        before_widget = QWidget()
        before_layout = QVBoxLayout(before_widget)
        before_layout.setContentsMargins(0, 0, 0, 0)
        before_layout.setSpacing(2)
        before_layout.addWidget(QLabel("Before"))
        self.before_text = QTextEdit()
        self.before_text.setReadOnly(True)
        before_layout.addWidget(self.before_text)
        preview_h_splitter.addWidget(before_widget)

        after_widget = QWidget()
        after_layout = QVBoxLayout(after_widget)
        after_layout.setContentsMargins(0, 0, 0, 0)
        after_layout.setSpacing(2)
        after_layout.addWidget(QLabel("After"))
        self.after_text = QTextEdit()
        self.after_text.setReadOnly(True)
        after_layout.addWidget(self.after_text)
        preview_h_splitter.addWidget(after_widget)

        preview_layout.addWidget(preview_h_splitter)

        self.match_label = QLabel("")
        preview_layout.addWidget(self.match_label)

        right_splitter.addWidget(preview_widget)

        # Log Area (Bottom Right)
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(2)
        log_layout.addWidget(QLabel("Log:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)

        right_splitter.addWidget(log_widget)

        # Set splitter sizes
        right_splitter.setSizes([400, 150])
        self.main_splitter.addWidget(right_splitter)
        self.main_splitter.setSizes([300, 800])

        # Apply initial theme
        self.apply_theme(is_dark=False)

    def toggle_theme(self, checked):
        self.apply_theme(is_dark=checked)

    def apply_theme(self, is_dark: bool):
        app = QApplication.instance()
        if not is_dark:
            # Clean, Antigravity-style Light Palette
            light_palette = QPalette()
            light_palette.setColor(QPalette.ColorRole.Window, QColor(245, 245, 245))
            light_palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.black)
            light_palette.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.white)
            light_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(250, 250, 250))
            light_palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
            light_palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.black)
            light_palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.black)
            light_palette.setColor(QPalette.ColorRole.Button, QColor(235, 235, 235))
            light_palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.black)
            light_palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
            light_palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
            light_palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
            light_palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)

            app.setPalette(light_palette)
            # Make splitters clearly visible and 4px wide/tall
            app.setStyleSheet("""
                QSplitter::handle {
                    background-color: #d4d4d4;
                    margin: 2px;
                    border-radius: 2px;
                }
                QSplitter::handle:horizontal {
                    width: 6px;
                }
                QSplitter::handle:vertical {
                    height: 6px;
                }
            """)
            return

        # Setup Dark Palette
        dark_palette = QPalette()
        dark_palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
        dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
        dark_palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)

        app.setPalette(dark_palette)

        # Adjust tooltip styling for dark mode readability and visible splitters
        app.setStyleSheet("""
            QToolTip { color: #ffffff; background-color: #2a82da; border: 1px solid white; }
            QSplitter::handle {
                background-color: #777777;
                margin: 2px;
                border-radius: 2px;
            }
            QSplitter::handle:horizontal {
                width: 6px;
            }
            QSplitter::handle:vertical {
                height: 6px;
            }
        """)

    # -----------------------------
    # Drag and Drop Events
    # -----------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path):
                # If a map file is dragged while Map Tab is open, load it as map file instead of setting folder
                if self.tabs.currentIndex() == 1 and path.lower().endswith(".txt"):
                    self._load_dropped_map_file(path)
                else:
                    # Otherwise treat it as selecting the parent folder for source files
                    self.source_folder = os.path.dirname(path)
                    self.folder_label.setText(f"Folder: {self.source_folder}")
                    self.on_rescan()
            elif os.path.isdir(path):
                self.source_folder = path
                self.folder_label.setText(f"Folder: {self.source_folder}")
                self.on_rescan()
        super().dropEvent(event)

    def _load_dropped_map_file(self, path: str):
        repl, warnings = load_replacements_map(path)
        if repl is None:
            QMessageBox.critical(self, "Error", warnings)
            return

        if not repl:
            QMessageBox.warning(self, "Empty map", "Map file is empty or has no valid pairs.")
            return

        duplicates = check_duplicate_new_names(repl)
        if duplicates:
            msg = "Pronađeni duplikati u 'novim' imenima:\n\n"
            for new, olds in duplicates.items():
                msg += f"'{new}' dobijaju: {', '.join(olds)}\n"
            QMessageBox.critical(self, "Duplikati pronađeni", msg)
            return

        self.map_file_path = path
        self.replacements_map = repl
        self.map_label.setText(f"Map file: {path}  ({len(repl)} pairs)")

        if warnings:
            self.log_text.append("Map file warnings:")
            for w in warnings:
                self.log_text.append(f"  - {w}")
            self.log_text.append("")

        self.clear_preview()

    def get_mode(self):
        return "single" if self.tabs.currentIndex() == 0 else "map"

    def on_mode_change(self):
        self.clear_preview()

    def on_select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if not folder:
            return
        self.source_folder = folder
        self.folder_label.setText(f"Folder: {folder}")
        self.on_rescan()

    def on_select_map(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select map file (old;new)",
            "",
            "Text files (*.txt);;All files (*.*)"
        )
        if not path:
            return

        repl, warnings = load_replacements_map(path)
        if repl is None:
            QMessageBox.critical(self, "Error", warnings)
            return

        if not repl:
            QMessageBox.warning(self, "Empty map", "Map file is empty or has no valid pairs.")
            return

        duplicates = check_duplicate_new_names(repl)
        if duplicates:
            msg = "Pronađeni duplikati u 'novim' imenima:\n\n"
            for new, olds in duplicates.items():
                msg += f"'{new}' dobijaju: {', '.join(olds)}\n"
            QMessageBox.critical(self, "Duplikati pronađeni", msg)
            return

        self.map_file_path = path
        self.replacements_map = repl
        self.map_label.setText(f"Map file: {path}  ({len(repl)} pairs)")

        if warnings:
            self.log_text.append("Map file warnings:")
            for w in warnings:
                self.log_text.append(f"  - {w}")
            self.log_text.append("")

        self.clear_preview()

    def on_rescan(self):
        if not self.source_folder:
            QMessageBox.warning(self, "No folder", "Please select a folder first.")
            return

        exts = parse_extensions(self.ext_entry.text())
        self.file_paths = scan_files(self.source_folder, self.include_sub.isChecked(), exts)

        self.file_list.clear()
        for p in self.file_paths:
            rel = os.path.relpath(p, self.source_folder)
            item = QListWidgetItem(rel)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.file_list.addItem(item)

        # Re-apply current search filter if any
        if self.search_input.text():
            self.on_search_changed(self.search_input.text())

        self.stats_label.setText(f"{len(self.file_paths)} files found")
        self.clear_preview()

    def on_search_changed(self, text: str):
        search_term = text.lower()
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            # If search term is empty or term is in the item text, show it. Otherwise hide it.
            item.setHidden(search_term not in item.text().lower())

    def select_all(self):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.CheckState.Checked)

    def select_none(self):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def clear_preview(self):
        self.before_text.clear()
        self.after_text.clear()
        self.match_label.setText("")

    def _validate_ready_for_preview(self):
        mode = self.get_mode()
        if mode == "single":
            old = self.old_entry.text()
            new = self.new_entry.text()
            return True, mode, old, new
        else:
            if not self.replacements_map:
                return False, mode, "", ""
            return True, mode, "", ""

    def on_file_select(self):
        if not self.source_folder or not self.file_paths:
            return

        selected_items = self.file_list.selectedItems()
        if not selected_items:
            self.clear_preview()
            return

        item = selected_items[0]
        idx = self.file_list.row(item)
        file_path = self.file_paths[idx]
        rel = os.path.relpath(file_path, self.source_folder)

        ok, mode, old, new = self._validate_ready_for_preview()

        text = read_text_file(file_path)
        if text is None:
            self.before_text.clear()
            self.after_text.clear()
            self.match_label.setText(f"{rel} (binary/unreadable skipped)")
            return

        self.before_text.clear()
        self.after_text.clear()

        if not ok:
            self.before_text.setPlainText("Select / load a map file to see preview.\n")
            self.after_text.setPlainText("Select / load a map file to see preview.\n")
            self.match_label.setText(f"{rel}")
            return

        if mode == "single":
            if not old:
                self.before_text.setPlainText("Enter 'Text to replace' to see preview.\n")
                self.after_text.setPlainText("Enter 'Text to replace' to see preview.\n")
                self.match_label.setText(f"{rel} — matches: 0")
                return

            count = text.count(old)
            examples = build_preview_single(text, old, new, max_examples=5, context=60)

            if count == 0:
                self.before_text.setPlainText("No matches in this file.\n")
                self.after_text.setPlainText("No changes.\n")
                self.match_label.setText(f"{rel} — matches: 0")
                return

            self.match_label.setText(f"{rel} — matches: {count} (showing up to {len(examples)} examples)")
            for i, (b, a) in enumerate(examples, start=1):
                self.before_text.append(f"--- Example {i} ---")
                self._append_html(self.before_text, b)
                self.before_text.append("")

                self.after_text.append(f"--- Example {i} ---")
                self._append_html(self.after_text, a)
                self.after_text.append("")

        else:
            total_after_text, total_count, per = apply_replacements(
                text, "map", "", "", self.replacements_map
            )
            examples = build_preview_map(text, self.replacements_map, max_examples=6, context=60)

            if total_count == 0:
                self.before_text.setPlainText("No matches for any map pair in this file.\n")
                self.after_text.setPlainText("No changes.\n")
                self.match_label.setText(f"{rel} — total matches: 0")
                return

            top_pairs = sorted(per.items(), key=lambda kv: kv[1], reverse=True)[:8]
            top_txt = ", ".join([f"{k} ({v})" for k, v in top_pairs])

            self.match_label.setText(f"{rel} — total matches: {total_count} | top: {top_txt}")

            if not examples:
                self.before_text.setPlainText("Matches exist, but no short examples could be built.\n")
                self.after_text.setPlainText("Try adjusting context or map pairs.\n")
                return

            for i, (label, b, a) in enumerate(examples, start=1):
                self.before_text.append(f"--- Example {i}: {label} ---")
                self._append_html(self.before_text, b)
                self.before_text.append("")

                self.after_text.append(f"--- Example {i}: {label} ---")
                self._append_html(self.after_text, a)
                self.after_text.append("")

    def _append_html(self, widget, html: str):
        """Append rich HTML content to a QTextEdit reliably using QTextCursor."""
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertBlock()
        cursor.insertHtml(html)
        widget.setTextCursor(cursor)

    def on_file_double_click(self, item: QListWidgetItem):
        idx = self.file_list.row(item)
        if idx >= 0 and idx < len(self.file_paths):
            file_path = self.file_paths[idx]
            # Use os.startfile on Windows to open the file completely asynchronously
            # avoiding any blocking behavior or tying to the parent process
            try:
                os.startfile(os.path.abspath(file_path))
            except Exception as e:
                pass

    def on_run(self):
        if not self.source_folder:
            QMessageBox.warning(self, "No folder", "Please select a folder first.")
            return

        mode = self.get_mode()

        if mode == "single":
            old = self.old_entry.text()
            new = self.new_entry.text()
            if not old:
                QMessageBox.warning(self, "Input error", "Text to replace cannot be empty.")
                return
        else:
            if not self.replacements_map:
                QMessageBox.warning(self, "Map missing", "Please select and load a map file first.")
                return

        selected_indices = []
        for i in range(self.file_list.count()):
            if self.file_list.item(i).checkState() == Qt.CheckState.Checked:
                selected_indices.append(i)

        if not selected_indices:
            QMessageBox.warning(self, "No files", "No files selected.")
            return

        folder_name = os.path.basename(self.source_folder.rstrip(os.sep))
        dest_folder = os.path.join(os.path.dirname(self.source_folder), folder_name + "_CLEAN")
        os.makedirs(dest_folder, exist_ok=True)

        self.log_text.clear()

        total_repl = 0
        changed_files = 0
        skipped = 0

        for idx in selected_indices:
            src_path = self.file_paths[idx]
            rel = os.path.relpath(src_path, self.source_folder)

            if mode == "map" and self.map_file_path:
                try:
                    if os.path.abspath(src_path) == os.path.abspath(self.map_file_path):
                        self.log_text.append(f"[SKIP] Map file: {rel}")
                        continue
                except Exception:
                    pass

            text = read_text_file(src_path)
            if text is None:
                skipped += 1
                continue

            if mode == "single":
                out_text, count, _ = apply_replacements(text, "single", old, new, {})
            else:
                out_text, count, _ = apply_replacements(text, "map", "", "", self.replacements_map)

            if count == 0:
                continue

            out_path = os.path.join(dest_folder, rel)
            write_text_file(out_path, out_text)

            total_repl += count
            changed_files += 1
            self.log_text.append(f"{rel}: {count} replacements")

        self.log_text.append("\n--- DONE ---")
        self.log_text.append(f"Changed files: {changed_files}")
        self.log_text.append(f"Total replacements: {total_repl}")
        if skipped:
            self.log_text.append(f"Skipped (binary/unreadable): {skipped}")
        self.log_text.append(f"Output folder: {dest_folder}")

        QMessageBox.information(self, "Completed", "Replacement finished. See log for details.")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = BulkReplaceApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
