"""
Boxed

Sorts files in a folder (default: ~/Downloads) into subfolders by file type.
You can change the folder in the GUI or pass a folder path on the command line.
Run manually whenever you want to tidy up. Existing files at the destination
are never overwritten -- matching filenames are skipped and reported.

Usage:
    python3 sort_downloads.py              # launches GUI
    python3 sort_downloads.py /path/to/folder
    python3 sort_downloads.py --dry-run
"""

import shutil
import sys
import threading
import tkinter as tk
import json
import os
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

try:
    from PIL import Image, ImageTk  # type: ignore[import-not-found]
except Exception:
    Image = None
    ImageTk = None

try:
    import cv2  # type: ignore[import-not-found]
except Exception:
    cv2 = None

# Map of category -> file extensions (lowercase, with dot)
DEFAULT_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".heic", ".tiff"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".pages"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods", ".numbers"},
    "Presentations": {".ppt", ".pptx", ".key", ".odp"},
    "Videos": {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"},
    "Audio": {".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"},
    "Installers": {".dmg", ".pkg", ".exe", ".msi", ".apk", ".deb", ".rpm"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".sh"},
}

# Reusable default extension arrays users can apply to any folder category.
DEFAULT_FILETYPE_ARRAYS = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".heic", ".tiff"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".pages"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods", ".numbers"},
    "Presentations": {".ppt", ".pptx", ".key", ".odp"},
    "Videos": {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"},
    "Audio": {".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"},
    "Installers": {".dmg", ".pkg", ".exe", ".msi", ".apk", ".deb", ".rpm"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".sh"},
}

OTHER_CATEGORY = "Other"
CONFIG_FILE = Path.home() / ".download_sorter_config.json"
PREVIEW_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"}
PREVIEW_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}


def normalize_extension(raw_ext: str):
    """Normalize user input like 'jpg' or '.JPG' to '.jpg'."""
    ext = raw_ext.strip().lower()
    if not ext:
        return None
    if not ext.startswith("."):
        ext = "." + ext
    if ext == ".":
        return None
    return ext


def normalize_folder_name(raw_name: str):
    """Normalize a folder path like 'code/python' to a clean relative path."""
    if raw_name is None:
        return None

    parts = []
    for part in raw_name.strip().replace("\\", "/").split("/"):
        segment = part.strip()
        if not segment:
            continue
        if segment in {".", ".."}:
            return None
        parts.append(segment)

    if not parts:
        return None
    return "/".join(parts)


def folder_parent_name(folder_name: str):
    parts = folder_name.split("/")
    if len(parts) <= 1:
        return None
    return "/".join(parts[:-1])


def folder_leaf_name(folder_name: str):
    return folder_name.split("/")[-1]


def rewrite_folder_prefix(folder_name: str, old_prefix: str, new_prefix: str):
    if folder_name == old_prefix:
        return new_prefix
    old_path_prefix = old_prefix + "/"
    if folder_name.startswith(old_path_prefix):
        suffix = folder_name[len(old_path_prefix):]
        return f"{new_prefix}/{suffix}"
    return folder_name


def remap_folder_keys(mapping, old_prefix: str, new_prefix: str):
    remapped = {}
    for key, value in mapping.items():
        remapped[rewrite_folder_prefix(key, old_prefix, new_prefix)] = value
    return remapped


def load_settings(config_path: Path = CONFIG_FILE):
    """Load categories and per-category filename filters from config."""
    if not config_path.exists():
        return {name: set(exts) for name, exts in DEFAULT_CATEGORIES.items()}, {}

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {name: set(exts) for name, exts in DEFAULT_CATEGORIES.items()}, {}

    raw_categories = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(raw_categories, dict):
        return {name: set(exts) for name, exts in DEFAULT_CATEGORIES.items()}, {}

    categories = {}
    for name, exts in raw_categories.items():
        if not isinstance(name, str):
            continue
        if not isinstance(exts, list):
            continue
        normalized_name = normalize_folder_name(name)
        if not normalized_name:
            continue
        normalized = {normalize_extension(e) for e in exts if isinstance(e, str)}
        normalized.discard(None)
        categories[normalized_name] = normalized

    raw_filters = data.get("category_name_filters") if isinstance(data, dict) else None
    category_name_filters = {}
    if isinstance(raw_filters, dict):
        for category_name, name_filter in raw_filters.items():
            if isinstance(category_name, str) and isinstance(name_filter, str):
                trimmed = name_filter.strip()
                normalized_name = normalize_folder_name(category_name)
                if trimmed and normalized_name:
                    category_name_filters[normalized_name] = trimmed

    if not categories:
        return {name: set(exts) for name, exts in DEFAULT_CATEGORIES.items()}, {}
    return categories, category_name_filters


def save_settings(categories, category_name_filters=None, config_path: Path = CONFIG_FILE):
    """Persist category mapping and per-category name filters to disk."""
    filters = category_name_filters or {}
    payload = {
        "categories": {
            category: sorted(extensions)
            for category, extensions in categories.items()
        },
        "category_name_filters": {
            category: name_filter.strip()
            for category, name_filter in filters.items()
            if category in categories and isinstance(name_filter, str) and name_filter.strip()
        },
    }
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_extension_lookup(categories):
    """Flatten categories into a single {extension: category} dict."""
    lookup = {}
    for category, extensions in categories.items():
        for ext in extensions:
            lookup[ext] = category
    return lookup


def is_folder_prefix(folder_name: str, prefix: str):
    return folder_name == prefix or folder_name.startswith(prefix + "/")


def get_suggested_category(file_path: Path, ext_lookup, category_name_filters=None):
    filters = category_name_filters or {}
    category = ext_lookup.get(file_path.suffix.lower(), OTHER_CATEGORY)
    category_filter = filters.get(category, "").strip().lower()
    if category != OTHER_CATEGORY and category_filter and category_filter not in file_path.name.lower():
        return OTHER_CATEGORY
    return category


def next_available_name(dest_dir: Path, file_name: str):
    """Return a non-conflicting filename by appending ' (n)' before suffix."""
    candidate = dest_dir / file_name
    if not candidate.exists():
        return file_name

    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    index = 1
    while True:
        renamed = f"{stem} ({index}){suffix}"
        if not (dest_dir / renamed).exists():
            return renamed
        index += 1


def build_sort_plan(target_dir: Path, categories, category_name_filters=None, overrides=None):
    """Build preview/apply plan for files directly in target_dir."""
    ext_lookup = build_extension_lookup(categories)
    overrides = overrides or {}

    files = [f for f in target_dir.iterdir() if f.is_file()]
    plan = []
    for file_path in files:
        if file_path.name == Path(__file__).name:
            continue

        suggested = get_suggested_category(file_path, ext_lookup, category_name_filters)
        chosen = overrides.get(file_path.name, suggested)
        if chosen not in categories and chosen != OTHER_CATEGORY:
            chosen = suggested

        dest_dir = target_dir / chosen
        dest_path = dest_dir / file_path.name
        exists = dest_path.exists()

        plan.append({
            "name": file_path.name,
            "source": file_path,
            "ext": file_path.suffix.lower(),
            "suggested": suggested,
            "chosen": chosen,
            "dest_dir": dest_dir,
            "dest_path": dest_path,
            "exists": exists,
        })
    return plan


def execute_sort_plan(plan, dry_run: bool = False, log=print):
    """Execute a prepared plan of moves."""
    moved = 0
    skipped = 0
    errors = 0

    for entry in plan:
        source = entry["source"]
        dest_dir = entry["dest_dir"]
        dest_path = entry["dest_path"]
        chosen = entry["chosen"]
        conflict_action = entry.get("conflict_action", "skip")

        if not source.exists():
            log(f"ERROR missing file: {source.name}")
            errors += 1
            continue

        if dest_path.exists():
            if conflict_action == "replace":
                try:
                    if dest_path.is_file():
                        dest_path.unlink()
                    else:
                        log(f"SKIP (existing target is not a file): {source.name} -> {chosen}/")
                        skipped += 1
                        continue
                except Exception as e:
                    log(f"ERROR replacing {source.name}: {e}")
                    errors += 1
                    continue
            else:
                log(f"SKIP (already exists): {source.name} -> {chosen}/")
                skipped += 1
                continue

        if dry_run:
            log(f"WOULD MOVE: {source.name} -> {chosen}/")
            moved += 1
            continue

        try:
            dest_dir.mkdir(exist_ok=True)
            shutil.move(str(source), str(dest_path))
            log(f"MOVED: {source.name} -> {chosen}/")
            moved += 1
        except Exception as e:
            log(f"ERROR moving {source.name}: {e}")
            errors += 1

    return moved, skipped, errors


def sort_folder(target_dir: Path, categories, category_name_filters=None, dry_run: bool = False, log=print):
    if not target_dir.is_dir():
        log(f"Error: '{target_dir}' is not a valid directory.")
        return

    plan = build_sort_plan(target_dir, categories, category_name_filters=category_name_filters)
    if not plan:
        log(f"No files found in '{target_dir}'. Nothing to do.")
        return

    moved, skipped, errors = execute_sort_plan(plan, dry_run=dry_run, log=log)

    log("\n--- Summary ---")
    action = "Would move" if dry_run else "Moved"
    log(f"{action}: {moved}")
    log(f"Skipped (duplicates): {skipped}")
    if errors:
        log(f"Errors: {errors}")


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    categories, category_name_filters = load_settings()

    # CLI mode when a path argument is provided
    if args:
        target_dir = Path(args[0]).expanduser().resolve()
        print(f"Sorting: {target_dir}")
        if dry_run:
            print("(Dry run -- no files will actually be moved)\n")
        sort_folder(target_dir, categories, category_name_filters=category_name_filters, dry_run=dry_run)
        return

    # GUI mode
    launch_gui(dry_run, categories, category_name_filters)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class SortApp:
    def __init__(self, root: tk.Tk, initial_dry_run: bool = False, initial_categories=None, initial_name_filters=None):
        self.root = root
        self.categories = initial_categories or {name: set(exts) for name, exts in DEFAULT_CATEGORIES.items()}
        self.category_name_filters = dict(initial_name_filters or {})
        self.manual_overrides = {}
        self.preview_plan = []

        root.title("Boxed")
        root.resizable(True, True)
        root.minsize(780, 420)

        # ── Folder row ──────────────────────────────────────────────────────
        folder_frame = ttk.Frame(root, padding=(10, 10, 10, 4))
        folder_frame.pack(fill=tk.X)

        ttk.Label(folder_frame, text="Folder to sort:").pack(side=tk.LEFT)

        self.folder_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        folder_entry = ttk.Entry(folder_frame, textvariable=self.folder_var, width=48)
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4))

        ttk.Button(folder_frame, text="Change…", command=self._browse).pack(side=tk.LEFT)

        # ── Options row ─────────────────────────────────────────────────────
        opt_frame = ttk.Frame(root, padding=(10, 2, 10, 6))
        opt_frame.pack(fill=tk.X)

        self.dry_run_var = tk.BooleanVar(value=initial_dry_run)
        ttk.Checkbutton(opt_frame, text="Dry run (preview only — no files moved)",
                        variable=self.dry_run_var).pack(side=tk.LEFT)

        self.preview_btn = ttk.Button(opt_frame, text="Refresh Preview", command=self._refresh_preview)
        self.preview_btn.pack(side=tk.RIGHT, padx=(0, 6))

        self.sort_btn = ttk.Button(opt_frame, text="Apply Moves", command=self._run_sort)
        self.sort_btn.pack(side=tk.RIGHT)

        # ── Main area ───────────────────────────────────────────────────────
        main_frame = ttk.Frame(root, padding=(10, 0, 10, 4))
        main_frame.pack(fill=tk.BOTH, expand=True)

        category_frame = ttk.LabelFrame(main_frame, text="Categories", padding=(8, 8, 8, 8))
        category_frame.pack(side=tk.LEFT, fill=tk.Y)

        self.category_list = tk.Listbox(category_frame, height=14, width=24, exportselection=False)
        self.category_list.pack(fill=tk.Y, expand=False)
        self.category_list.bind("<Button-3>", self._show_category_menu)

        self.category_menu = tk.Menu(root, tearoff=0)
        self.category_menu.add_command(label="Add Folder", command=self._add_category)
        self.category_menu.add_command(label="Edit Folder Name", command=self._rename_category)
        self.category_menu.add_command(label="Select Parent Folder", command=self._select_parent_folder)
        self.category_menu.add_command(label="Delete Folder", command=self._delete_category)
        self.category_menu.add_separator()
        self.category_menu.add_command(label="Properties", command=self._open_category_properties)

        self._refresh_category_list()

        # ── Preview area ────────────────────────────────────────────────────
        preview_frame = ttk.LabelFrame(main_frame, text="Preview", padding=(8, 8, 8, 8))
        preview_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        columns = ("ext", "suggested", "chosen", "status")
        self.preview_tree = ttk.Treeview(preview_frame, columns=columns, show="tree headings")
        self.preview_tree.heading("#0", text="File")
        self.preview_tree.heading("ext", text="Type")
        self.preview_tree.heading("suggested", text="Suggested Folder")
        self.preview_tree.heading("chosen", text="Final Folder")
        self.preview_tree.heading("status", text="Status")
        self.preview_tree.column("#0", width=260, stretch=True)
        self.preview_tree.column("ext", width=80, anchor=tk.W)
        self.preview_tree.column("suggested", width=130, anchor=tk.W)
        self.preview_tree.column("chosen", width=120, anchor=tk.W)
        self.preview_tree.column("status", width=160, anchor=tk.W)
        self.preview_tree.pack(fill=tk.BOTH, expand=True)
        self.preview_tree.bind("<Button-3>", self._show_preview_menu)

        self.preview_menu = tk.Menu(root, tearoff=0)
        self.preview_set_menu = tk.Menu(self.preview_menu, tearoff=0)
        self.preview_menu.add_cascade(label="Set Destination Folder", menu=self.preview_set_menu)
        self.preview_menu.add_command(label="Use Suggested Folder", command=self._reset_selected_preview_destination)
        self.preview_menu.add_separator()
        self.preview_menu.add_command(label="Preview Media", command=self._preview_selected_media)

        self.media_preview_win = None

        # ── Status bar ──────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready.")
        status_bar = ttk.Label(root, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W, padding=(6, 2))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self._refresh_preview()

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _browse(self):
        chosen = filedialog.askdirectory(initialdir=self.folder_var.get(),
                                        title="Select folder to sort")
        if chosen:
            self.folder_var.set(chosen)
            self._refresh_preview()

    def _refresh_category_list(self):
        selected_name = self._selected_category_name()
        self.category_list.delete(0, tk.END)
        for name in self.categories.keys():
            self.category_list.insert(tk.END, name)

        if not self.categories:
            return

        names = list(self.categories.keys())
        if selected_name in names:
            idx = names.index(selected_name)
        else:
            idx = 0
        self.category_list.selection_set(idx)
        self.category_list.activate(idx)

    def _selected_category_name(self):
        sel = self.category_list.curselection()
        if not sel:
            return None
        return self.category_list.get(sel[0])

    def _save_categories_with_feedback(self):
        try:
            save_settings(self.categories, self.category_name_filters)
        except Exception as exc:
            messagebox.showerror("Save Error", f"Could not save settings:\n{exc}")
            return False
        return True

    def _rename_folder_path(self, old_name: str, new_name: str):
        old_name = normalize_folder_name(old_name)
        new_name = normalize_folder_name(new_name)
        if not old_name or not new_name:
            messagebox.showwarning("Invalid Name", "Folder names cannot be empty.", parent=self.root)
            return False
        if new_name == old_name:
            return False
        if new_name.startswith(old_name + "/"):
            messagebox.showwarning(
                "Invalid Parent Folder",
                "A folder cannot be moved inside itself.",
                parent=self.root,
            )
            return False
        if new_name in self.categories and new_name != old_name:
            messagebox.showwarning("Duplicate", "That folder category already exists.", parent=self.root)
            return False

        remapped_categories = remap_folder_keys(self.categories, old_name, new_name)
        if len(remapped_categories) != len(self.categories):
            messagebox.showwarning(
                "Duplicate",
                "That move would collide with an existing folder path.",
                parent=self.root,
            )
            return False

        self.categories = remapped_categories
        self.category_name_filters = remap_folder_keys(self.category_name_filters, old_name, new_name)
        self.manual_overrides = {
            file_name: rewrite_folder_prefix(folder_name, old_name, new_name)
            for file_name, folder_name in self.manual_overrides.items()
        }

        if self._save_categories_with_feedback():
            self._refresh_category_list()
            self._refresh_preview()
            return True
        return False

    def _show_category_menu(self, event):
        idx = self.category_list.nearest(event.y)
        if idx < 0:
            return
        self.category_list.selection_clear(0, tk.END)
        self.category_list.selection_set(idx)
        self.category_list.activate(idx)
        self.category_menu.tk_popup(event.x_root, event.y_root)

    def _add_category(self):
        name = simpledialog.askstring("Add Folder Category", "Folder name:", parent=self.root)
        if not name:
            return
        name = normalize_folder_name(name)
        if not name:
            messagebox.showwarning(
                "Invalid Name",
                "Enter a folder name like 'code' or 'code/python'.",
                parent=self.root,
            )
            return
        if name.lower() == OTHER_CATEGORY.lower():
            messagebox.showwarning("Invalid Name", f"'{OTHER_CATEGORY}' is reserved for uncategorized files.")
            return
        if name in self.categories:
            messagebox.showwarning("Duplicate", "That folder category already exists.")
            return

        self.categories[name] = set()
        self.category_name_filters.setdefault(name, "")
        if self._save_categories_with_feedback():
            self._refresh_category_list()
            self._refresh_preview()
            self.status_var.set(f"Added category '{name}'.")

    def _rename_category(self):
        old_name = self._selected_category_name()
        if not old_name:
            messagebox.showinfo("Rename Category", "Select a category first.")
            return

        new_name = simpledialog.askstring("Rename Folder Category", "New folder name:",
                                          initialvalue=old_name, parent=self.root)
        if not new_name:
            return
        new_name = normalize_folder_name(new_name)
        if not new_name:
            messagebox.showwarning(
                "Invalid Name",
                "Enter a folder name like 'code' or 'code/python'.",
                parent=self.root,
            )
            return
        if new_name.lower() == OTHER_CATEGORY.lower():
            messagebox.showwarning("Invalid Name", f"'{OTHER_CATEGORY}' is reserved for uncategorized files.")
            return
        if self._rename_folder_path(old_name, new_name):
            self.status_var.set(f"Renamed '{old_name}' to '{new_name}'.")

    def _select_parent_folder(self):
        old_name = self._selected_category_name()
        if not old_name:
            messagebox.showinfo("Select Parent Folder", "Select a category first.")
            return

        allowed_parents = [
            name for name in self.categories.keys()
            if not is_folder_prefix(name, old_name)
        ]
        current_parent = folder_parent_name(old_name) or ""

        dialog = SelectParentFolderDialog(
            self.root,
            folder_name=old_name,
            parent_options=allowed_parents,
            initial_parent=current_parent,
        )
        if dialog.result is None:
            return

        new_parent = dialog.result
        new_name = folder_leaf_name(old_name)
        if new_parent:
            new_name = f"{new_parent}/{new_name}"

        if self._rename_folder_path(old_name, new_name):
            self.status_var.set(f"Moved '{old_name}' under '{new_parent or 'top level'}'.")

    def _delete_category(self):
        name = self._selected_category_name()
        if not name:
            messagebox.showinfo("Delete Category", "Select a category first.")
            return

        confirm = messagebox.askyesno(
            "Delete Folder Category",
            f"Delete '{name}'?\n\nFiles that matched this category will go to '{OTHER_CATEGORY}' unless reassigned."
        )
        if not confirm:
            return

        self.categories.pop(name, None)
        self.category_name_filters.pop(name, None)
        self.manual_overrides = {
            file_name: folder_name
            for file_name, folder_name in self.manual_overrides.items()
            if folder_name != name
        }
        if self._save_categories_with_feedback():
            self._refresh_category_list()
            self._refresh_preview()
            self.status_var.set(f"Deleted category '{name}'.")

    def _open_category_properties(self):
        name = self._selected_category_name()
        if not name:
            messagebox.showinfo("Category Properties", "Select a category first.")
            return
        CategoryPropertiesDialog(self.root, name, self.categories, self.category_name_filters, self._on_categories_updated)

    def _on_categories_updated(self):
        if self._save_categories_with_feedback():
            self._refresh_category_list()
            self._refresh_preview()
            self.status_var.set("Category properties updated.")

    def _show_preview_menu(self, event):
        row_id = self.preview_tree.identify_row(event.y)
        if not row_id:
            return
        self.preview_tree.selection_set(row_id)

        self.preview_set_menu.delete(0, tk.END)
        folder_options = list(self.categories.keys()) + [OTHER_CATEGORY]
        for folder_name in folder_options:
            self.preview_set_menu.add_command(
                label=folder_name,
                command=lambda name=folder_name: self._set_selected_preview_destination(name),
            )

        self.preview_menu.tk_popup(event.x_root, event.y_root)

    def _selected_preview_name(self):
        sel = self.preview_tree.selection()
        if not sel:
            return None
        row_id = sel[0]
        values = self.preview_tree.item(row_id, "values")
        if not values:
            return None
        return self.preview_tree.item(row_id, "text")

    def _selected_preview_entry(self):
        file_name = self._selected_preview_name()
        if not file_name:
            return None
        for entry in self.preview_plan:
            if entry["name"] == file_name:
                return entry
        return None

    def _set_selected_preview_destination(self, folder_name: str):
        file_name = self._selected_preview_name()
        if not file_name:
            return
        self.manual_overrides[file_name] = folder_name
        self._refresh_preview()

    def _reset_selected_preview_destination(self):
        file_name = self._selected_preview_name()
        if not file_name:
            return
        self.manual_overrides.pop(file_name, None)
        self._refresh_preview()

    def _preview_selected_media(self):
        entry = self._selected_preview_entry()
        if not entry:
            return

        source_path = entry["source"]
        if not source_path.exists():
            messagebox.showinfo("Preview", "Selected file no longer exists.", parent=self.root)
            return

        if self.media_preview_win is None or not self.media_preview_win.win.winfo_exists():
            self.media_preview_win = MediaPreviewWindow(self.root)
        self.media_preview_win.show_file(source_path)

    def _refresh_preview(self):
        target_dir = Path(self.folder_var.get()).expanduser().resolve()
        for row in self.preview_tree.get_children():
            self.preview_tree.delete(row)

        if not target_dir.is_dir():
            self.preview_plan = []
            self.status_var.set("Select a valid folder to preview.")
            return

        try:
            plan = build_sort_plan(
                target_dir,
                self.categories,
                category_name_filters=self.category_name_filters,
                overrides=self.manual_overrides,
            )
        except Exception as exc:
            self.preview_plan = []
            self.status_var.set(f"Could not build preview: {exc}")
            return

        file_names_in_plan = {entry["name"] for entry in plan}
        self.manual_overrides = {
            file_name: folder_name
            for file_name, folder_name in self.manual_overrides.items()
            if file_name in file_names_in_plan
        }
        self.preview_plan = plan

        for entry in sorted(plan, key=lambda x: x["name"].lower()):
            status = "Will skip (exists)" if entry["exists"] else "Ready"
            self.preview_tree.insert(
                "",
                tk.END,
                text=entry["name"],
                values=(entry["ext"] or "(none)", entry["suggested"], entry["chosen"], status),
            )

        self.status_var.set(f"Preview ready: {len(plan)} files.")

    def _run_sort(self):
        target_dir = Path(self.folder_var.get()).expanduser().resolve()
        dry_run = self.dry_run_var.get()

        self._refresh_preview()
        if not self.preview_plan:
            self.status_var.set("No files to move.")
            return

        execution_plan = self._resolve_conflicts_for_run(self.preview_plan, dry_run)
        if execution_plan is None:
            self.status_var.set("Move cancelled.")
            return
        if not execution_plan:
            self.status_var.set("No files selected for moving.")
            return

        self.sort_btn.configure(state=tk.DISABLED)
        self.preview_btn.configure(state=tk.DISABLED)
        mode_label = "DRY RUN — " if dry_run else ""
        self.status_var.set(f"{mode_label}Sorting {target_dir} …")

        def worker():
            lines = []
            moved, skipped, errors = execute_sort_plan(
                execution_plan,
                dry_run=dry_run,
                log=lines.append,
            )

            def done():
                self.sort_btn.configure(state=tk.NORMAL)
                self.preview_btn.configure(state=tk.NORMAL)
                self._refresh_preview()
                action = "Would move" if dry_run else "Moved"
                summary = f"{action}: {moved} | Skipped: {skipped}"
                if errors:
                    summary += f" | Errors: {errors}"
                self.status_var.set(summary)
                if errors or dry_run:
                    messagebox.showinfo("Sort Summary", "\n".join(lines + ["", summary]), parent=self.root)
            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _resolve_conflicts_for_run(self, plan, dry_run: bool):
        if dry_run:
            return list(plan)

        resolved = []
        planned_paths = set()
        for entry in plan:
            cloned = dict(entry)
            dest_dir = cloned["dest_dir"]
            dest_path = cloned["dest_path"]

            # Also detect conflicts inside the current batch.
            has_batch_conflict = str(dest_path).lower() in planned_paths
            has_disk_conflict = dest_path.exists()
            if not has_disk_conflict and not has_batch_conflict:
                cloned["conflict_action"] = "none"
                resolved.append(cloned)
                planned_paths.add(str(dest_path).lower())
                continue

            action = messagebox.askyesnocancel(
                "Name Conflict",
                (
                    f"Destination already has '{dest_path.name}' in '{cloned['chosen']}'.\n\n"
                    "Yes = Replace existing\n"
                    "No = Rename moved file\n"
                    "Cancel = Skip this file"
                ),
                parent=self.root,
            )

            if action is None:
                continue
            if action is True:
                cloned["conflict_action"] = "replace"
                resolved.append(cloned)
                planned_paths.add(str(dest_path).lower())
                continue

            suggested_name = next_available_name(dest_dir, cloned["name"])
            while True:
                new_name = simpledialog.askstring(
                    "Rename File",
                    "Enter new filename:",
                    initialvalue=suggested_name,
                    parent=self.root,
                )
                if new_name is None:
                    break

                new_name = new_name.strip()
                if not new_name:
                    messagebox.showinfo("Rename File", "Filename cannot be empty.", parent=self.root)
                    continue

                ext = Path(new_name).suffix
                if not ext and cloned["ext"]:
                    new_name = new_name + cloned["ext"]

                new_dest = dest_dir / new_name
                lowered = str(new_dest).lower()
                if new_dest.exists() or lowered in planned_paths:
                    messagebox.showinfo("Rename File", "That name already exists. Choose another name.", parent=self.root)
                    continue

                cloned["dest_path"] = new_dest
                cloned["conflict_action"] = "none"
                resolved.append(cloned)
                planned_paths.add(lowered)
                break

        return resolved


class MediaPreviewWindow:
    def __init__(self, parent):
        self.parent = parent
        self.win = tk.Toplevel(parent)
        self.win.title("Media Preview")
        self.win.geometry("920x700")
        self.win.minsize(540, 380)

        outer = ttk.Frame(self.win, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)

        self.title_var = tk.StringVar(value="No file selected")
        ttk.Label(outer, textvariable=self.title_var).pack(anchor=tk.W, pady=(0, 6))

        self.preview_label = ttk.Label(outer, anchor=tk.CENTER)
        self.preview_label.pack(fill=tk.BOTH, expand=True)

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(actions, text="Open Externally", command=self._open_external).pack(side=tk.RIGHT)

        self.current_file = None
        self._photo = None

    def show_file(self, file_path: Path):
        self.current_file = file_path
        self.title_var.set(str(file_path.name))
        self.win.deiconify()
        self.win.lift()

        ext = file_path.suffix.lower()
        if ext in PREVIEW_IMAGE_EXTS:
            self._show_image(file_path)
            return
        if ext in PREVIEW_VIDEO_EXTS:
            self._show_video_frame(file_path)
            return

        self._show_text("Preview is only available for image and video files.")

    def _show_text(self, message: str):
        self._photo = None
        self.preview_label.configure(image="", text=message)

    def _show_image(self, file_path: Path):
        if Image is None or ImageTk is None:
            self._show_text("Image preview needs Pillow. Install with: pip install pillow")
            return
        try:
            with Image.open(file_path) as img:
                img.thumbnail((880, 620))
                self._photo = ImageTk.PhotoImage(img.copy())
            self.preview_label.configure(image=self._photo, text="")
        except Exception as exc:
            self._show_text(f"Could not preview image: {exc}")

    def _show_video_frame(self, file_path: Path):
        if cv2 is None or Image is None or ImageTk is None:
            self._show_text("Video preview needs OpenCV + Pillow. Install with: pip install opencv-python pillow")
            return
        try:
            cap = cv2.VideoCapture(str(file_path))
            ok, frame = cap.read()
            cap.release()
            if not ok:
                self._show_text("Could not read a video frame for preview.")
                return

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame)
            img.thumbnail((880, 620))
            self._photo = ImageTk.PhotoImage(img)
            self.preview_label.configure(image=self._photo, text="")
        except Exception as exc:
            self._show_text(f"Could not preview video: {exc}")

    def _open_external(self):
        if not self.current_file:
            return
        try:
            os.startfile(str(self.current_file))
        except Exception as exc:
            messagebox.showerror("Open Externally", f"Could not open file: {exc}", parent=self.win)


class SelectParentFolderDialog:
    def __init__(self, parent, folder_name: str, parent_options, initial_parent: str = ""):
        self.result = None

        self.win = tk.Toplevel(parent)
        self.win.title("Select Parent Folder")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()

        outer = ttk.Frame(self.win, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text=f"Choose a parent folder for '{folder_name}'").pack(anchor=tk.W)
        ttk.Label(outer, text="Leave blank to keep it at the top level.").pack(anchor=tk.W, pady=(0, 6))

        options = [""] + sorted({normalize_folder_name(name) for name in parent_options if normalize_folder_name(name)}, key=str.lower)
        self.parent_var = tk.StringVar(value=initial_parent if initial_parent in options else "")
        self.parent_combo = ttk.Combobox(outer, textvariable=self.parent_var, values=options, state="readonly", width=32)
        self.parent_combo.pack(fill=tk.X)

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_row, text="Cancel", command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="OK", command=self._ok).pack(side=tk.RIGHT, padx=(0, 6))

        self.win.protocol("WM_DELETE_WINDOW", self._cancel)
        self.parent_combo.focus_set()
        self.win.wait_window(self.win)

    def _ok(self):
        value = self.parent_var.get().strip()
        self.result = value or None
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class CategoryPropertiesDialog:
    def __init__(self, parent, category_name: str, categories, category_name_filters, on_change):
        self.parent = parent
        self.category_name = category_name
        self.categories = categories
        self.category_name_filters = category_name_filters
        self.on_change = on_change

        self.win = tk.Toplevel(parent)
        self.win.title(f"Properties - {category_name}")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()

        outer = ttk.Frame(self.win, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text=f"File types for '{category_name}'").pack(anchor=tk.W)

        self.ext_list = tk.Listbox(outer, height=12, width=28, exportselection=False)
        self.ext_list.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        self.ext_list.bind("<Button-3>", self._show_ext_menu)
        self._refresh_ext_list()

        filter_frame = ttk.LabelFrame(outer, text="Folder Name Filter", padding=(8, 6, 8, 6))
        filter_frame.pack(fill=tk.X, pady=(4, 0))

        self.filter_enabled_var = tk.BooleanVar(
            value=bool(self.category_name_filters.get(self.category_name, "").strip())
        )
        ttk.Checkbutton(
            filter_frame,
            text="Only sort files containing:",
            variable=self.filter_enabled_var,
        ).pack(side=tk.LEFT)

        self.filter_value_var = tk.StringVar(value=self.category_name_filters.get(self.category_name, ""))
        self.filter_entry = ttk.Entry(filter_frame, textvariable=self.filter_value_var, width=20)
        self.filter_entry.pack(side=tk.LEFT, padx=(6, 0))

        ttk.Button(filter_frame, text="Save", command=self._save_name_filter).pack(side=tk.RIGHT)

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_row, text="Clear All Filetypes", command=self._clear_all_extensions).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(btn_row, text="Close", command=self.win.destroy).pack(side=tk.RIGHT)

        self.ext_menu = tk.Menu(self.win, tearoff=0)
        self.ext_menu.add_command(label="Add New", command=self._add_extension)
        self.ext_menu.add_command(label="Remove Selected", command=self._remove_selected_extension)

        defaults_submenu = tk.Menu(self.ext_menu, tearoff=0)
        for preset_name in DEFAULT_FILETYPE_ARRAYS.keys():
            defaults_submenu.add_command(
                label=f"Apply {preset_name}",
                command=lambda name=preset_name: self._apply_default_array(name),
            )
        self.ext_menu.add_cascade(label="Apply Default Array", menu=defaults_submenu)

    def _refresh_ext_list(self):
        self.ext_list.delete(0, tk.END)
        for ext in sorted(self.categories.get(self.category_name, set())):
            self.ext_list.insert(tk.END, ext)

    def _show_ext_menu(self, event):
        idx = self.ext_list.nearest(event.y)
        if idx >= 0 and self.ext_list.size() > 0:
            self.ext_list.selection_clear(0, tk.END)
            self.ext_list.selection_set(idx)
            self.ext_list.activate(idx)
        self.ext_menu.tk_popup(event.x_root, event.y_root)

    def _add_extension(self):
        raw = simpledialog.askstring(
            "Add File Type",
            "Enter file extension (example: .psd or psd):",
            parent=self.win,
        )
        if not raw:
            return

        ext = normalize_extension(raw)
        if not ext:
            messagebox.showwarning("Invalid Extension", "Please enter a valid extension.", parent=self.win)
            return

        # Ensure one extension maps to only one category.
        for category, extensions in self.categories.items():
            if category != self.category_name and ext in extensions:
                move = messagebox.askyesno(
                    "Reassign Extension",
                    f"{ext} is currently assigned to '{category}'.\nMove it to '{self.category_name}'?",
                    parent=self.win,
                )
                if not move:
                    return
                extensions.remove(ext)
                break

        self.categories[self.category_name].add(ext)
        self._refresh_ext_list()
        self.on_change()

    def _remove_selected_extension(self):
        sel = self.ext_list.curselection()
        if not sel:
            return
        ext = self.ext_list.get(sel[0])
        self.categories[self.category_name].discard(ext)
        self._refresh_ext_list()
        self.on_change()

    def _apply_default_array(self, preset_name: str):
        preset_exts = DEFAULT_FILETYPE_ARRAYS.get(preset_name, set())
        if not preset_exts:
            return

        # Move extensions from other categories before applying preset.
        for ext in preset_exts:
            for category, extensions in self.categories.items():
                if category != self.category_name and ext in extensions:
                    extensions.remove(ext)

        self.categories[self.category_name].update(preset_exts)
        self._refresh_ext_list()
        self.on_change()

    def _clear_all_extensions(self):
        confirm = messagebox.askyesno(
            "Clear Filetypes",
            f"Remove all filetypes from '{self.category_name}'?",
            parent=self.win,
        )
        if not confirm:
            return
        self.categories[self.category_name].clear()
        self._refresh_ext_list()
        self.on_change()

    def _save_name_filter(self):
        if not self.filter_enabled_var.get():
            self.category_name_filters.pop(self.category_name, None)
            self.on_change()
            return

        value = self.filter_value_var.get().strip()
        if not value:
            messagebox.showinfo("Folder Name Filter", "Enter text to match, or disable the filter.", parent=self.win)
            return
        self.category_name_filters[self.category_name] = value
        self.on_change()


def launch_gui(initial_dry_run: bool = False, categories=None, category_name_filters=None):
    root = tk.Tk()
    SortApp(
        root,
        initial_dry_run=initial_dry_run,
        initial_categories=categories,
        initial_name_filters=category_name_filters,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
