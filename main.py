import os
import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox,
    QTabWidget, QSplitter, QListWidget, QTextEdit, QFileDialog,
    QMessageBox, QListWidgetItem, QFrame,
    QPlainTextEdit, QStatusBar, QToolButton
)
from PyQt6.QtGui import QPalette, QColor, QTextCursor, QTextCharFormat, QIcon
from PyQt6.QtCore import Qt

import html
import re
import shutil


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

    newline = ""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            if "\r\n" in text:
                newline = "\r\n"
            elif "\n" in text:
                newline = "\n"
            elif "\r" in text:
                newline = "\r"
            return text, enc, newline
        except UnicodeDecodeError:
            continue

    return None


def write_text_file(path: str, text: str, encoding: str = "utf-8", newline: str = ""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding, newline="") as f:
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
# App resources
# -----------------------------

def resource_path(*parts: str) -> str:
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, *parts)


def load_app_icon() -> QIcon:
    icon_path = resource_path("assets", "app_icon.ico")
    if os.path.exists(icon_path):
        return QIcon(icon_path)
    return QIcon()


# -----------------------------
# Replacement map helpers
# -----------------------------

def load_replacements_map(map_path: str):
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception as e:
        return None, f"Ne mogu da procitam map file:\n{e}"

    repl = {}
    warnings = []

    for line_no, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ";" not in line:
            warnings.append(f"Preskacem liniju {line_no} (nema ';' ): {line}")
            continue

        old, new = line.split(";", 1)
        old, new = old.strip(), new.strip()

        if not old:
            warnings.append(f"Preskacem liniju {line_no} (prazan 'stari' string): {line}")
            continue

        if old in repl and repl[old] != new:
            warnings.append(
                f"Konflikt za '{old}' na liniji {line_no}: zadrzavam '{repl[old]}', preskacem '{new}'"
            )
            continue
        if old in repl:
            warnings.append(f"Duplikat za '{old}' na liniji {line_no}: isti target, preskacem")
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


def _make_pattern(old: str, case_sensitive: bool, whole_word: bool) -> re.Pattern:
    """Compile a regex pattern with the given flags."""
    pattern = re.escape(old)
    if whole_word:
        pattern = r'\b' + pattern + r'\b'
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(pattern, flags)



def _make_preview_payload(snippet: str, spans: list[tuple[int, int]], bg_color: str, text_color: str):
    clean_spans = []
    cursor = 0
    for start, end in sorted(spans):
        start = max(0, min(start, len(snippet)))
        end = max(start, min(end, len(snippet)))
        if start < cursor:
            continue
        clean_spans.append((start, end))
        cursor = end
    return snippet, clean_spans, bg_color, text_color

def _expand_preview_to_lines(text: str, start: int, end: int, pad_lines: int = 1):
    left = start
    right = end

    for _ in range(pad_lines):
        prev_nl = text.rfind("\n", 0, left - 1 if left > 0 else 0)
        if prev_nl == -1:
            left = 0
            break
        left = prev_nl

    if left > 0 and text[left] == "\n":
        left += 1

    for _ in range(pad_lines + 1):
        next_nl = text.find("\n", right)
        if next_nl == -1:
            right = len(text)
            break
        right = next_nl + 1

    return left, right


def _group_hits_for_preview(text: str, hits: list[tuple], pad_lines: int = 1):
    groups = []
    for hit in hits:
        start, end = hit[0], hit[1]
        left, right = _expand_preview_to_lines(text, start, end, pad_lines=pad_lines)
        if groups and left <= groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], right)
            groups[-1][2].append(hit)
        else:
            groups.append([left, right, [hit]])
    return groups


def _build_single_group_preview(text: str, group_hits: list[tuple], left: int, right: int, replacement: str):
    snippet = text[left:right]
    before_spans = []
    after_parts = []
    after_spans = []
    cursor = left
    out_cursor = 0

    for start, end, matched in group_hits:
        before_spans.append((start - left, end - left))
        unchanged = text[cursor:start]
        after_parts.append(unchanged)
        out_cursor += len(unchanged)
        after_parts.append(replacement)
        after_spans.append((out_cursor, out_cursor + len(replacement)))
        out_cursor += len(replacement)
        cursor = end

    after_parts.append(text[cursor:right])
    after_snippet = "".join(after_parts)
    return (
        _make_preview_payload(snippet, before_spans, "#ffe2b8", "#7a4300"),
        _make_preview_payload(after_snippet, after_spans, "#cfeecf", "#165b2a"),
    )


def build_preview_single(
    text: str, old: str, new: str,
    case_sensitive: bool = True, whole_word: bool = False,
    max_examples: int = 5, context: int = 60
):
    examples = []
    if not old:
        return examples
    try:
        pattern = _make_pattern(old, case_sensitive, whole_word)
    except re.error:
        return examples

    hits = [(m.start(), m.end(), m.group(0)) for m in pattern.finditer(text)]
    if not hits:
        return examples

    for left, right, group_hits in _group_hits_for_preview(text, hits, pad_lines=1)[:max_examples]:
        examples.append(_build_single_group_preview(text, group_hits, left, right, new))

    return examples


def _collect_non_overlapping_map_hits(
    text: str, replacements: dict,
    case_sensitive: bool = True, whole_word: bool = False
):
    all_hits = []
    for old, new in replacements.items():
        try:
            pattern = _make_pattern(old, case_sensitive, whole_word)
        except re.error:
            continue
        for m in pattern.finditer(text):
            all_hits.append((m.start(), m.end(), m.group(0), old, new))

    all_hits.sort(key=lambda h: (h[0], -(h[1] - h[0]), h[3]))

    accepted = []
    cursor = 0
    for hit in all_hits:
        start, end, _, _, _ = hit
        if start < cursor:
            continue
        accepted.append(hit)
        cursor = end

    return accepted


def _build_map_group_preview(text: str, group_hits: list[tuple], left: int, right: int):
    snippet = text[left:right]
    before_spans = []
    after_parts = []
    after_spans = []
    cursor = left
    out_cursor = 0
    labels = []

    for start, end, matched, old, new in group_hits:
        before_spans.append((start - left, end - left))
        unchanged = text[cursor:start]
        after_parts.append(unchanged)
        out_cursor += len(unchanged)
        after_parts.append(new)
        after_spans.append((out_cursor, out_cursor + len(new)))
        out_cursor += len(new)
        cursor = end
        labels.append(f"{old} -> {new}")

    after_parts.append(text[cursor:right])
    after_snippet = "".join(after_parts)
    label = " | ".join(labels)
    return (
        label,
        _make_preview_payload(snippet, before_spans, "#ffe2b8", "#7a4300"),
        _make_preview_payload(after_snippet, after_spans, "#cfeecf", "#165b2a"),
    )


def build_preview_map(
    text: str, replacements: dict,
    case_sensitive: bool = True, whole_word: bool = False,
    max_examples: int = 10, context: int = 60
):
    if not replacements:
        return []

    hits = _collect_non_overlapping_map_hits(text, replacements, case_sensitive, whole_word)
    if not hits:
        return []

    out = []
    for left, right, group_hits in _group_hits_for_preview(text, hits, pad_lines=1)[:max_examples]:
        out.append(_build_map_group_preview(text, group_hits, left, right))
    return out


def apply_replacements(
    text: str, mode: str, old: str, new: str, replacements_map: dict,
    case_sensitive: bool = True, whole_word: bool = False
):
    """Returns (new_text, total_count, per_pair_dict)."""
    if mode == "single":
        if not old:
            return text, 0, {}
        try:
            pattern = _make_pattern(old, case_sensitive, whole_word)
        except re.error:
            return text, 0, {}
        c = len(pattern.findall(text))
        result = pattern.sub(new, text)
        return result, c, {f"{old} -> {new}": c}

    if not replacements_map:
        return text, 0, {}

    hits = _collect_non_overlapping_map_hits(text, replacements_map, case_sensitive, whole_word)
    if not hits:
        return text, 0, {}

    out_parts = []
    per = {}
    cursor = 0
    for start, end, _, old_key, new_value in hits:
        out_parts.append(text[cursor:start])
        out_parts.append(new_value)
        cursor = end
        pair_key = f"{old_key} -> {new_value}"
        per[pair_key] = per.get(pair_key, 0) + 1
    out_parts.append(text[cursor:])

    return "".join(out_parts), len(hits), per


# -----------------------------
# Styled widgets
# -----------------------------

class SectionHeader(QLabel):
    """Small uppercase section label used above panels."""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("SectionHeader")


class PillButton(QPushButton):
    """Standard action button with rounded corners via stylesheet."""
    def __init__(self, text: str, variant: str = "default", parent=None):
        super().__init__(text, parent)
        self.variant = variant
        self.setObjectName(f"PillButton_{variant}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class FolderBar(QFrame):
    """Top bar showing selected folder path."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FolderBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        icon_lbl = QLabel("DIR")
        icon_lbl.setObjectName("FolderTag")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setFixedWidth(36)
        layout.addWidget(icon_lbl)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("No folder selected - drag and drop a folder here or click Browse")
        self.path_edit.setObjectName("FolderPathEdit")
        layout.addWidget(self.path_edit, 1)

        self.btn_browse = PillButton("Browse...", "secondary")
        self.btn_browse.setFixedWidth(90)
        layout.addWidget(self.btn_browse)


# -----------------------------
# GUI app
# -----------------------------

class BulkReplaceApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bulk Replace with Preview")
        self.setWindowIcon(load_app_icon())
        self.resize(1280, 760)
        self.setMinimumSize(900, 560)
        self.setAcceptDrops(True)

        self.source_folder = ""
        self.file_paths: list[str] = []
        self.map_file_path = ""
        self.replacements_map = {}
        self._is_dark = False
        self._before_example_positions = []
        self._after_example_positions = []
        self._syncing_preview = False
        self._preview_sync_enabled = False

        # Status bar
        self.status_bar = QStatusBar()
        self.status_bar.setObjectName("AppStatusBar")
        self.setStatusBar(self.status_bar)
        self._status_file_count = QLabel("No folder loaded")
        self._status_match = QLabel("")
        self.status_bar.addWidget(self._status_file_count)
        self.status_bar.addPermanentWidget(self._status_match)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        self.folder_bar = FolderBar()
        self.folder_bar.btn_browse.clicked.connect(self.on_select_folder)
        root.addWidget(self.folder_bar)
        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)
        opts_row.setContentsMargins(0, 2, 0, 2)

        ext_label = QLabel("Extensions:")
        ext_label.setObjectName("OptionLabel")
        opts_row.addWidget(ext_label)

        self.ext_entry = QLineEdit()
        self.ext_entry.setText(".txt,.md,.html,.xhtml,.css,.sql,.ddl,.dml,.psql,.mysql,.plsql,.tsql,.prc,.fnc,.vw")
        self.ext_entry.setObjectName("ExtEntry")
        self.ext_entry.setMinimumWidth(300)
        opts_row.addWidget(self.ext_entry, 1)
        self.ext_entry.textChanged.connect(self._auto_rescan)

        self.include_sub = QCheckBox("Include subfolders")
        self.include_sub.setChecked(True)
        self.include_sub.toggled.connect(self._auto_rescan)
        opts_row.addWidget(self.include_sub)

        opts_row.addStretch()

        self.theme_btn = QToolButton()
        self.theme_btn.setText("Light")
        self.theme_btn.setObjectName("ThemeToggle")
        self.theme_btn.setCheckable(True)
        self.theme_btn.setChecked(False)
        self.theme_btn.toggled.connect(self._on_theme_toggle)
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        opts_row.addWidget(self.theme_btn)

        root.addLayout(opts_row)
        root.addWidget(self._make_divider())
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ReplaceTabs")
        self.tabs.setDocumentMode(False)

        # Single tab
        single_tab = QWidget()
        single_layout = QHBoxLayout(single_tab)
        single_layout.setContentsMargins(12, 10, 12, 10)
        single_layout.setSpacing(14)

        single_layout.addWidget(QLabel("Find:"))
        self.old_entry = QLineEdit()
        self.old_entry.setPlaceholderText("Text to replace...")
        self.old_entry.setObjectName("FindEntry")
        self.old_entry.textChanged.connect(self.on_file_select)
        single_layout.addWidget(self.old_entry, 1)

        arrow = QLabel("->")
        arrow.setObjectName("ArrowLabel")
        single_layout.addWidget(arrow)

        single_layout.addWidget(QLabel("Replace with:"))
        self.new_entry = QLineEdit()
        self.new_entry.setPlaceholderText("Replacement text...")
        self.new_entry.setObjectName("ReplaceEntry")
        self.new_entry.textChanged.connect(self.on_file_select)
        single_layout.addWidget(self.new_entry, 1)
        # shared match options live in the control row below tabs

        self.cb_case = QCheckBox("Aa  Case")
        self.cb_case.setChecked(False)
        self.cb_case.setToolTip("Case sensitive matching")
        self.cb_case.toggled.connect(self.on_file_select)

        self.cb_word = QCheckBox("[ ]  Whole word")
        self.cb_word.setChecked(False)
        self.cb_word.setToolTip("Match whole words only (e.g. BOB won't match BOBBY)")
        self.cb_word.toggled.connect(self.on_file_select)



        self.tabs.addTab(single_tab, "  Single Replace  ")

        # Map tab
        map_tab = QWidget()
        map_layout = QHBoxLayout(map_tab)
        map_layout.setContentsMargins(12, 10, 12, 10)
        map_layout.setSpacing(12)

        self.map_label = QLabel("No map file loaded")
        self.map_label.setObjectName("MapLabel")
        map_layout.addWidget(self.map_label, 1)

        btn_sel_map = PillButton("Load map file...", "secondary")
        btn_sel_map.clicked.connect(self.on_select_map)
        map_layout.addWidget(btn_sel_map)

        self.tabs.addTab(map_tab, "  Map File (old;new)  ")
        self.tabs.currentChanged.connect(self.on_mode_change)

        root.addWidget(self.tabs)

        control_row = QHBoxLayout()
        control_row.setSpacing(10)
        control_row.addWidget(self.cb_case)
        control_row.addWidget(self.cb_word)
        control_row.addStretch()
        self.btn_run = PillButton("Run Replace on Selected Files", "primary")
        self.btn_run.clicked.connect(self.on_run)
        control_row.addWidget(self.btn_run)
        root.addLayout(control_row)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setHandleWidth(6)
        self.main_splitter.setChildrenCollapsible(False)
        root.addWidget(self.main_splitter, 1)
        left_widget = QWidget()
        left_widget.setObjectName("LeftPane")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(SectionHeader("FILES"))

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter files...")
        self.search_input.setObjectName("SearchInput")
        self.search_input.textChanged.connect(self.on_search_changed)
        left_layout.addWidget(self.search_input)

        self.file_list = QListWidget()
        self.file_list.setObjectName("FileList")
        self.file_list.setAlternatingRowColors(True)
        self.file_list.itemSelectionChanged.connect(self.on_file_select)
        self.file_list.itemDoubleClicked.connect(self.on_file_double_click)
        left_layout.addWidget(self.file_list, 1)

        sel_btns = QHBoxLayout()
        sel_btns.setSpacing(6)
        btn_all = PillButton("Select All", "ghost")
        btn_all.clicked.connect(self.select_all)
        sel_btns.addWidget(btn_all)

        btn_none = PillButton("Select None", "ghost")
        btn_none.clicked.connect(self.select_none)
        sel_btns.addWidget(btn_none)
        left_layout.addLayout(sel_btns)

        self.main_splitter.addWidget(left_widget)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setHandleWidth(6)
        right_splitter.setChildrenCollapsible(False)

        # Preview
        preview_widget = QWidget()
        preview_widget.setObjectName("PreviewPane")
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)

        preview_layout.addWidget(SectionHeader("PREVIEW"))

        preview_h_splitter = QSplitter(Qt.Orientation.Horizontal)
        preview_h_splitter.setHandleWidth(4)

        # Before panel
        before_frame = QFrame()
        before_frame.setObjectName("BeforeFrame")
        before_vl = QVBoxLayout(before_frame)
        before_vl.setContentsMargins(0, 0, 0, 0)
        before_vl.setSpacing(0)

        before_header = QLabel("  BEFORE")
        before_header.setObjectName("BeforeHeader")
        before_vl.addWidget(before_header)

        self.before_text = QTextEdit()
        self.before_text.setReadOnly(True)
        self.before_text.setObjectName("PreviewText")
        before_vl.addWidget(self.before_text, 1)
        self.before_text.cursorPositionChanged.connect(self._sync_from_before_preview)
        preview_h_splitter.addWidget(before_frame)
        # After panel
        after_frame = QFrame()
        after_frame.setObjectName("AfterFrame")
        after_vl = QVBoxLayout(after_frame)
        after_vl.setContentsMargins(0, 0, 0, 0)
        after_vl.setSpacing(0)

        after_header = QLabel("  AFTER")
        after_header.setObjectName("AfterHeader")
        after_vl.addWidget(after_header)

        self.after_text = QTextEdit()
        self.after_text.setReadOnly(True)
        self.after_text.setObjectName("PreviewText")
        after_vl.addWidget(self.after_text, 1)
        self.after_text.cursorPositionChanged.connect(self._sync_from_after_preview)
        preview_h_splitter.addWidget(after_frame)

        preview_layout.addWidget(preview_h_splitter, 1)

        self.match_label = QLabel("")
        self.match_label.setObjectName("MatchLabel")
        preview_layout.addWidget(self.match_label)

        right_splitter.addWidget(preview_widget)

        # Log panel
        log_widget = QWidget()
        log_widget.setObjectName("LogPane")
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)

        log_layout.addWidget(SectionHeader("LOG"))

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("LogText")
        log_layout.addWidget(self.log_text, 1)

        right_splitter.addWidget(log_widget)
        right_splitter.setSizes([520, 160])

        self.main_splitter.addWidget(right_splitter)
        self.main_splitter.setSizes([300, 900])

        # Apply initial theme
        self.apply_theme(is_dark=False)

    def _make_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("Divider")
        return line

    def _make_vline(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setObjectName("Divider")
        return line

    def _on_theme_toggle(self, checked: bool):
        self._is_dark = checked
        self.theme_btn.setText("Dark" if checked else "Light")
        self.apply_theme(is_dark=checked)

    def apply_theme(self, is_dark: bool):
        app = QApplication.instance()

        if is_dark:
            bg          = "#1e1e2e"
            bg_mid      = "#252535"
            bg_input    = "#16161f"
            bg_alt      = "#1a1a28"
            fg          = "#cdd6f4"
            fg_muted    = "#7f849c"
            border      = "#45475a"
            accent      = "#89b4fa"
            accent_fg   = "#1e1e2e"
            primary_btn = "#89b4fa"
            primary_fg  = "#1e1e2e"
            before_hdr  = "#3d2a1a"
            before_txt  = "#e07b00"
            after_hdr   = "#1a3020"
            after_txt   = "#2e9e4f"
            splitter_c  = "#313244"
            tab_bg      = "#252535"
            tab_sel     = "#1e1e2e"
            log_bg      = "#16161f"
        else:
            bg          = "#f4f4f8"
            bg_mid      = "#ffffff"
            bg_input    = "#ffffff"
            bg_alt      = "#eeeef4"
            fg          = "#2a2a3d"
            fg_muted    = "#888899"
            border      = "#d0d0de"
            accent      = "#4c6ef5"
            accent_fg   = "#ffffff"
            primary_btn = "#4c6ef5"
            primary_fg  = "#ffffff"
            before_hdr  = "#fff3e0"
            before_txt  = "#c25a00"
            after_hdr   = "#e8f5e9"
            after_txt   = "#1b7a36"
            splitter_c  = "#d0d0de"
            tab_bg      = "#ebebf3"
            tab_sel     = "#ffffff"
            log_bg      = "#fafafa"

        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window,         QColor(bg))
        palette.setColor(QPalette.ColorRole.WindowText,     QColor(fg))
        palette.setColor(QPalette.ColorRole.Base,           QColor(bg_input))
        palette.setColor(QPalette.ColorRole.AlternateBase,  QColor(bg_alt))
        palette.setColor(QPalette.ColorRole.Text,           QColor(fg))
        palette.setColor(QPalette.ColorRole.Button,         QColor(bg_mid))
        palette.setColor(QPalette.ColorRole.ButtonText,     QColor(fg))
        palette.setColor(QPalette.ColorRole.Highlight,      QColor(accent))
        palette.setColor(QPalette.ColorRole.HighlightedText,QColor(accent_fg))
        palette.setColor(QPalette.ColorRole.ToolTipBase,    QColor(bg_mid))
        palette.setColor(QPalette.ColorRole.ToolTipText,    QColor(fg))
        app.setPalette(palette)

        mono = "Consolas, 'Courier New', monospace"

        app.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {bg};
                color: {fg};
                font-family: 'Segoe UI', 'SF Pro Display', Helvetica, Arial, sans-serif;
                font-size: 13px;
            }}
            #FolderBar {{
                background: {bg_mid};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            #FolderTag {{
                color: {accent};
                background: {bg_input};
                border: 1px solid {border};
                border-radius: 4px;
                font-size: 10px;
                font-weight: 700;
                padding: 3px 0;
                min-width: 36px;
            }}
            #FolderPathEdit {{
                background: transparent;
                border: none;
                color: {fg};
                font-size: 12px;
                padding: 0 4px;
            }}
            #FolderPathEdit:focus {{ outline: none; border: none; }}
            #OptionLabel {{ color: {fg_muted}; font-size: 12px; }}
            #ExtEntry {{
                background: {bg_input};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px 8px;
                color: {fg};
                font-size: 12px;
                font-family: {mono};
            }}
            #ExtEntry:focus {{ border-color: {accent}; }}
            #ThemeToggle {{
                background: transparent;
                border: 1px solid {border};
                border-radius: 14px;
                padding: 3px 12px;
                color: {fg_muted};
                font-size: 12px;
            }}
            #ThemeToggle:hover {{ border-color: {accent}; color: {accent}; }}
            #ThemeToggle:checked {{ background: {accent}; color: {accent_fg}; border-color: {accent}; }}
            #Divider {{ background: {border}; max-height: 1px; border: none; }}
            #ReplaceTabs {{
                background: {bg};
            }}
            #ReplaceTabs::pane {{
                border: 1px solid {border};
                border-radius: 0 6px 6px 6px;
                background: {tab_sel};
                top: -1px;
            }}
            #ReplaceTabs QTabBar {{
                background: transparent;
            }}
            #ReplaceTabs QTabBar::tab {{
                background: {tab_bg};
                color: {fg_muted};
                border: 1px solid {border};
                border-bottom: none;
                border-radius: 6px 6px 0 0;
                padding: 7px 18px;
                margin-right: 3px;
                font-size: 12px;
                font-weight: 500;
                min-width: 120px;
            }}
            #ReplaceTabs QTabBar::tab:selected {{
                background: {tab_sel};
                color: {accent};
                border-bottom: 2px solid {accent};
                font-weight: 600;
            }}
            #ReplaceTabs QTabBar::tab:hover:!selected {{ color: {fg}; }}
            #FindEntry, #ReplaceEntry {{
                background: {bg_input};
                border: 1px solid {border};
                border-radius: 5px;
                padding: 6px 10px;
                color: {fg};
                font-size: 13px;
            }}
            #FindEntry:focus, #ReplaceEntry:focus {{ border-color: {accent}; }}
            #ArrowLabel {{ color: {fg_muted}; font-size: 16px; font-weight: 300; }}
            #MapLabel {{ color: {fg_muted}; font-size: 12px; font-style: italic; }}
            #PillButton_primary {{
                background: {primary_btn};
                color: {primary_fg};
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-weight: 700;
                font-size: 13px;
                letter-spacing: 0.3px;
            }}
            #PillButton_primary:hover {{ background: {accent}; }}
            #PillButton_primary:pressed {{ opacity: 0.85; }}

            #PillButton_secondary {{
                background: {bg_mid};
                color: {fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 7px 16px;
                font-size: 13px;
            }}
            #PillButton_secondary:hover {{ border-color: {accent}; color: {accent}; }}

            #PillButton_ghost {{
                background: transparent;
                color: {fg_muted};
                border: 1px solid {border};
                border-radius: 5px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            #PillButton_ghost:hover {{ color: {fg}; border-color: {fg_muted}; }}
            #SectionHeader {{
                color: {fg_muted};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1.2px;
                padding: 4px 2px 2px 2px;
            }}
            #LeftPane {{ padding: 0; }}
            #SearchInput {{
                background: {bg_input};
                border: 1px solid {border};
                border-radius: 5px;
                padding: 6px 10px;
                color: {fg};
                font-size: 12px;
            }}
            #SearchInput:focus {{ border-color: {accent}; }}

            #FileList {{
                background: {bg_input};
                border: 1px solid {border};
                border-radius: 5px;
                alternate-background-color: {bg_alt};
                outline: none;
            }}
            #FileList::item {{
                padding: 5px 8px;
                border-radius: 3px;
                font-size: 12px;
                font-family: {mono};
            }}
            #FileList::item:selected {{
                background: {accent};
                color: {accent_fg};
            }}
            #FileList::item:hover:!selected {{ background: {bg_alt}; }}
            #BeforeHeader {{
                background: {before_hdr};
                color: {before_txt};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 5px 10px;
                border-bottom: 1px solid {border};
            }}
            #AfterHeader {{
                background: {after_hdr};
                color: {after_txt};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 5px 10px;
                border-bottom: 1px solid {border};
            }}
            #PreviewText {{
                background: {bg_input};
                border: 1px solid {border};
                border-top: none;
                font-family: {mono};
                font-size: 12px;
                color: {fg};
                padding: 6px;
            }}
            #MatchLabel {{
                color: {fg_muted};
                font-size: 11px;
                padding: 2px 4px;
                font-family: {mono};
            }}
            #LogText {{
                background: {log_bg};
                border: 1px solid {border};
                border-radius: 5px;
                font-family: {mono};
                font-size: 11px;
                color: {fg};
                padding: 6px;
            }}
            QSplitter::handle {{
                background: {splitter_c};
                border-radius: 3px;
            }}
            QSplitter::handle:horizontal {{ width: 6px; }}
            QSplitter::handle:vertical   {{ height: 6px; }}
            QSplitter::handle:hover {{ background: {accent}; }}
            QScrollBar:vertical {{
                background: {bg_alt};
                width: 8px;
                border-radius: 4px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {border};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {accent}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar:horizontal {{
                background: {bg_alt};
                height: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:horizontal {{
                background: {border};
                border-radius: 4px;
                min-width: 24px;
            }}
            QScrollBar::handle:horizontal:hover {{ background: {accent}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
            QCheckBox {{
                spacing: 6px;
                font-size: 12px;
                color: {fg};
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border: 1px solid {border};
                border-radius: 3px;
                background: {bg_input};
            }}
            QCheckBox::indicator:checked {{
                background: {accent};
                border-color: {accent};
            }}
            #AppStatusBar {{
                background: {bg_mid};
                border-top: 1px solid {border};
                color: {fg_muted};
                font-size: 11px;
                padding: 0 8px;
            }}
        """)

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
                if self.tabs.currentIndex() == 1 and path.lower().endswith(".txt"):
                    self._load_dropped_map_file(path)
                else:
                    self.source_folder = os.path.dirname(path)
                    self.folder_bar.path_edit.setText(self.source_folder)
                    self.on_rescan()
            elif os.path.isdir(path):
                self.source_folder = path
                self.folder_bar.path_edit.setText(path)
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
            msg = "Pronadjeni duplikati u 'novim' imenima:\n\n"
            for new, olds in duplicates.items():
                msg += f"'{new}' dobijaju: {', '.join(olds)}\n"
            QMessageBox.critical(self, "Duplikati pronadjeni", msg)
            return

        self.map_file_path = path
        self.replacements_map = repl
        name = os.path.basename(path)
        self.map_label.setText(f"OK: {name}   ({len(repl)} pairs)")

        if warnings:
            self.log_text.appendPlainText("Map file warnings:")
            for w in warnings:
                self.log_text.appendPlainText(f"  - {w}")
            self.log_text.appendPlainText("")

        if self.source_folder:
            self.on_rescan()
        else:
            self._select_first_visible_file()

    def get_mode(self):
        return "single" if self.tabs.currentIndex() == 0 else "map"

    def on_mode_change(self):
        self._select_first_visible_file()

    def on_select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if not folder:
            return
        self.source_folder = folder
        self.folder_bar.path_edit.setText(folder)
        self.on_rescan()

    def on_select_map(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select map file (old;new)", "",
            "Text files (*.txt);;All files (*.*)"
        )
        if not path:
            return

        self._load_dropped_map_file(path)

    def _auto_rescan(self, *_):
        if self.source_folder:
            self.on_rescan()

    def _select_first_visible_file(self):
        if self.file_list.count() == 0:
            self.clear_preview()
            return

        selected = self.file_list.selectedItems()
        if selected:
            item = selected[0]
            if not item.isHidden() and item.checkState() == Qt.CheckState.Checked:
                self.on_file_select()
                return

        first_visible = None
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.isHidden():
                continue
            if first_visible is None:
                first_visible = item
            if item.checkState() == Qt.CheckState.Checked:
                self.file_list.setCurrentItem(item)
                self.on_file_select()
                return

        if first_visible is not None:
            self.file_list.setCurrentItem(first_visible)
            self.on_file_select()
            return

        self.clear_preview()

    def _should_check_file_by_default(self, path: str) -> bool:
        if not self.map_file_path:
            return True
        try:
            return os.path.abspath(path) != os.path.abspath(self.map_file_path)
        except Exception:
            return True

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
            item.setCheckState(Qt.CheckState.Checked if self._should_check_file_by_default(p) else Qt.CheckState.Unchecked)
            self.file_list.addItem(item)

        if self.search_input.text():
            self.on_search_changed(self.search_input.text())
        self._select_first_visible_file()
        count = len(self.file_paths)
        self._status_file_count.setText(f"  {count} file{'s' if count != 1 else ''} found")

    def on_search_changed(self, text: str):
        term = text.lower()
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            item.setHidden(term not in item.text().lower())
        self._select_first_visible_file()

    def select_all(self):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.CheckState.Checked)


    def select_none(self):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def clear_preview(self):
        self._before_example_positions = []
        self._after_example_positions = []
        self.before_text.clear()
        self.after_text.clear()
        self.match_label.setText("")
        self._status_match.setText("")

    def _validate_ready_for_preview(self):
        mode = self.get_mode()
        if mode == "single":
            return True, mode, self.old_entry.text(), self.new_entry.text()
        else:
            if not self.replacements_map:
                return False, mode, "", ""
            return True, mode, "", ""

    def _preview_example_index_for_cursor(self, widget: QTextEdit, positions: list[int]):
        if not positions:
            return None
        block_number = widget.textCursor().blockNumber()
        idx = 0
        for i, start_block in enumerate(positions):
            if start_block <= block_number:
                idx = i
            else:
                break
        return idx

    def _sync_preview_to_example(self, source: QTextEdit, target: QTextEdit, source_positions: list[int], target_positions: list[int]):
        if self._syncing_preview or not self._preview_sync_enabled:
            return
        idx = self._preview_example_index_for_cursor(source, source_positions)
        if idx is None or idx >= len(target_positions):
            return
        self._syncing_preview = True
        try:
            block = target.document().findBlockByNumber(target_positions[idx])
            if not block.isValid():
                return
            cursor = target.textCursor()
            cursor.setPosition(block.position())
            target.setTextCursor(cursor)
            target.ensureCursorVisible()
        finally:
            self._syncing_preview = False

    def _sync_from_before_preview(self):
        self._sync_preview_to_example(self.before_text, self.after_text, self._before_example_positions, self._after_example_positions)

    def _sync_from_after_preview(self):
        self._sync_preview_to_example(self.after_text, self.before_text, self._after_example_positions, self._before_example_positions)

    def _append_preview_example(self, widget: QTextEdit, positions: list[int], header: str, preview_payload):
        start_block = self._append_example_header(widget, header)
        positions.append(start_block)
        self._append_preview_content(widget, preview_payload)

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

        file_info = read_text_file(file_path)
        if file_info is None:
            self.before_text.clear()
            self.after_text.clear()
            msg = f"{rel}  (binary / unreadable - skipped)"
            self.match_label.setText(msg)
            self._status_match.setText(msg)
            return

        text, _, _ = file_info

        self._preview_sync_enabled = False
        try:
            self.clear_preview()

            if not ok:
                self.before_text.setPlainText("Select / load a map file to see preview.")
                self.after_text.setPlainText("Select / load a map file to see preview.")
                self.match_label.setText(rel)
                return

            if mode == "single":
                if not old:
                    self.before_text.setPlainText("Enter 'Find' text to see preview.")
                    self.after_text.setPlainText("Enter 'Find' text to see preview.")
                    self.match_label.setText(f"{rel}  -  matches: 0")
                    return

                cs = self.cb_case.isChecked()
                ww = self.cb_word.isChecked()

                try:
                    pat = _make_pattern(old, cs, ww)
                except re.error:
                    self.before_text.setPlainText("Invalid search pattern.")
                    self.after_text.setPlainText("")
                    return

                count = len(pat.findall(text))
                examples = build_preview_single(
                    text, old, new,
                    case_sensitive=cs,
                    whole_word=ww,
                    max_examples=5,
                    context=60,
                )

                if count == 0:
                    self.before_text.setPlainText("No matches in this file.")
                    self.after_text.setPlainText("No changes.")
                    msg = f"{rel}  -  matches: 0"
                    self.match_label.setText(msg)
                    self._status_match.setText(msg)
                    return

                msg = f"{rel}  -  {count} match{'es' if count != 1 else ''}  (showing up to {len(examples)} examples)"
                self.match_label.setText(msg)
                self._status_match.setText(msg)

                for i, (b, a) in enumerate(examples, start=1):
                    self._append_preview_example(self.before_text, self._before_example_positions, f"-- Example {i} --", b)
                    self._append_preview_example(self.after_text, self._after_example_positions, f"-- Example {i} --", a)
            else:
                cs = self.cb_case.isChecked()
                ww = self.cb_word.isChecked()

                _, total_count, per = apply_replacements(
                    text, "map", "", "", self.replacements_map,
                    case_sensitive=cs, whole_word=ww
                )
                examples = build_preview_map(
                    text, self.replacements_map,
                    case_sensitive=cs, whole_word=ww,
                    max_examples=20, context=60
                )

                if total_count == 0:
                    self.before_text.setPlainText("No matches for any map pair in this file.")
                    self.after_text.setPlainText("No changes.")
                    msg = f"{rel}  -  total matches: 0"
                    self.match_label.setText(msg)
                    self._status_match.setText(msg)
                    return

                shown = len(examples)
                suffix = f"  (showing {shown} of {total_count})" if shown < total_count else f"  ({shown} shown)"
                top_pairs = sorted(per.items(), key=lambda kv: kv[1], reverse=True)[:6]
                top_txt = ",  ".join([f"{k}: {v}" for k, v in top_pairs])
                msg = f"{rel}  -  {total_count} total{suffix}  |  {top_txt}"
                self.match_label.setText(msg)
                self._status_match.setText(f"{rel}  -  {total_count} matches")

                if not examples:
                    self.before_text.setPlainText("Matches exist, but no short examples could be built.")
                    self.after_text.setPlainText("Try adjusting context or map pairs.")
                    return

                for i, (label, b, a) in enumerate(examples, start=1):
                    self._append_preview_example(self.before_text, self._before_example_positions, f"-- {i}. {label} --", b)
                    self._append_preview_example(self.after_text, self._after_example_positions, f"-- {i}. {label} --", a)
        finally:
            self._preview_sync_enabled = True

    def _append_preview_content(self, widget: QTextEdit, preview_payload):
        snippet, spans, bg_color, text_color = preview_payload
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        plain_format = QTextCharFormat()
        plain_format.setForeground(widget.palette().text().color())

        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor(bg_color))
        highlight_format.setForeground(QColor(text_color))

        snippet_cursor = 0
        for start, end in spans:
            if start > snippet_cursor:
                cursor.insertText(snippet[snippet_cursor:start], plain_format)
            cursor.insertText(snippet[start:end], highlight_format)
            snippet_cursor = end

        if snippet_cursor < len(snippet):
            cursor.insertText(snippet[snippet_cursor:], plain_format)

        cursor.insertBlock()
        widget.setTextCursor(cursor)

    def _append_example_header(self, widget: QTextEdit, text: str):
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if cursor.position() > 0:
            cursor.insertBlock()
            cursor.insertBlock()

        header_format = QTextCharFormat()
        header_format.setForeground(QColor("#888899"))
        cursor.insertText(text, header_format)
        cursor.insertBlock()
        widget.setTextCursor(cursor)
        return cursor.blockNumber()

    def on_file_double_click(self, item: QListWidgetItem):
        idx = self.file_list.row(item)
        if 0 <= idx < len(self.file_paths):
            try:
                os.startfile(os.path.abspath(self.file_paths[idx]))
            except Exception:
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
                QMessageBox.warning(self, "Input error", "Find text cannot be empty.")
                return
        else:
            if not self.replacements_map:
                QMessageBox.warning(self, "Map missing", "Please load a map file first.")
                return

        selected_indices = [
            i for i in range(self.file_list.count())
            if self.file_list.item(i).checkState() == Qt.CheckState.Checked
        ]

        if not selected_indices:
            QMessageBox.warning(self, "No files", "No files selected.")
            return

        folder_name = os.path.basename(self.source_folder.rstrip(os.sep))
        dest_folder = os.path.join(os.path.dirname(self.source_folder), folder_name + "_CLEAN")

        self.log_text.clear()
        if os.path.isdir(dest_folder):
            shutil.rmtree(dest_folder)
        os.makedirs(dest_folder, exist_ok=True)

        total_repl = 0
        changed_files = 0
        skipped = 0

        for idx in selected_indices:
            src_path = self.file_paths[idx]
            rel = os.path.relpath(src_path, self.source_folder)

            if mode == "map" and self.map_file_path:
                try:
                    if os.path.abspath(src_path) == os.path.abspath(self.map_file_path):
                        self.log_text.appendPlainText(f"[SKIP] Map file: {rel}")
                        continue
                except Exception:
                    pass

            file_info = read_text_file(src_path)
            if file_info is None:
                skipped += 1
                continue

            text, src_encoding, src_newline = file_info

            if mode == "single":
                out_text, count, _ = apply_replacements(
                    text, "single", old, new, {},
                    case_sensitive=self.cb_case.isChecked(),
                    whole_word=self.cb_word.isChecked()
                )
            else:
                out_text, count, _ = apply_replacements(
                    text, "map", "", "", self.replacements_map,
                    case_sensitive=self.cb_case.isChecked(),
                    whole_word=self.cb_word.isChecked()
                )

            if count == 0:
                continue

            out_path = os.path.join(dest_folder, rel)
            write_text_file(out_path, out_text, encoding=src_encoding, newline=src_newline)

            total_repl += count
            changed_files += 1
            self.log_text.appendPlainText(f"{rel}: {count} replacement{'s' if count != 1 else ''}")

        self.log_text.appendPlainText("\n-- DONE -----------------------------")
        self.log_text.appendPlainText(f"Changed files   : {changed_files}")
        self.log_text.appendPlainText(f"Total replacements: {total_repl}")
        if skipped:
            self.log_text.appendPlainText(f"Skipped (binary): {skipped}")
        self.log_text.appendPlainText(f"Output folder   : {dest_folder}")

        self._status_file_count.setText(
            f"  Done - {changed_files} file{'s' if changed_files != 1 else ''} changed, "
            f"{total_repl} replacements"
        )

        QMessageBox.information(self, "Completed", "Replacement finished.\nSee the log panel for details.")

def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(load_app_icon())
    app.setStyle("Fusion")
    window = BulkReplaceApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()




