"""
Felix Bryant
Download Sorter

Sorts files in a folder (default: ~/Downloads) into subfolders by file type.
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
from tkinter import filedialog, scrolledtext, ttk
from pathlib import Path

# Map of category -> file extensions (lowercase, with dot)
CATEGORIES = {
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


def build_extension_lookup():
    """Flatten CATEGORIES into a single {extension: category} dict."""
    lookup = {}
    for category, extensions in CATEGORIES.items():
        for ext in extensions:
            lookup[ext] = category
    return lookup


def sort_folder(target_dir: Path, dry_run: bool = False, log=print):
    if not target_dir.is_dir():
        log(f"Error: '{target_dir}' is not a valid directory.")
        return

    ext_lookup = build_extension_lookup()

    moved = 0
    skipped = 0
    errors = 0

    # Only look at files directly inside target_dir (not already-sorted subfolders)
    files = [f for f in target_dir.iterdir() if f.is_file()]

    if not files:
        log(f"No files found in '{target_dir}'. Nothing to do.")
        return

    for file_path in files:
        # Don't try to sort the script itself if it happens to live there
        if file_path.name == Path(__file__).name:
            continue

        category = ext_lookup.get(file_path.suffix.lower(), OTHER_CATEGORY)
        dest_dir = target_dir / category
        dest_path = dest_dir / file_path.name

        if dest_path.exists():
            log(f"SKIP (already exists): {file_path.name} -> {category}/")
            skipped += 1
            continue

        if dry_run:
            log(f"WOULD MOVE: {file_path.name} -> {category}/")
            moved += 1
            continue

        try:
            dest_dir.mkdir(exist_ok=True)
            shutil.move(str(file_path), str(dest_path))
            log(f"MOVED: {file_path.name} -> {category}/")
            moved += 1
        except Exception as e:
            log(f"ERROR moving {file_path.name}: {e}")
            errors += 1

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

    # CLI mode when a path argument is provided
    if args:
        target_dir = Path(args[0]).expanduser().resolve()
        print(f"Sorting: {target_dir}")
        if dry_run:
            print("(Dry run -- no files will actually be moved)\n")
        sort_folder(target_dir, dry_run=dry_run)
        return

    # GUI mode
    launch_gui(dry_run)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class SortApp:
    def __init__(self, root: tk.Tk, initial_dry_run: bool = False):
        self.root = root
        root.title("Download Sorter")
        root.resizable(True, True)
        root.minsize(540, 400)

        # ── Folder row ──────────────────────────────────────────────────────
        folder_frame = ttk.Frame(root, padding=(10, 10, 10, 4))
        folder_frame.pack(fill=tk.X)

        ttk.Label(folder_frame, text="Folder:").pack(side=tk.LEFT)

        self.folder_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        folder_entry = ttk.Entry(folder_frame, textvariable=self.folder_var, width=48)
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4))

        ttk.Button(folder_frame, text="Browse…", command=self._browse).pack(side=tk.LEFT)

        # ── Options row ─────────────────────────────────────────────────────
        opt_frame = ttk.Frame(root, padding=(10, 2, 10, 6))
        opt_frame.pack(fill=tk.X)

        self.dry_run_var = tk.BooleanVar(value=initial_dry_run)
        ttk.Checkbutton(opt_frame, text="Dry run (preview only — no files moved)",
                        variable=self.dry_run_var).pack(side=tk.LEFT)

        self.sort_btn = ttk.Button(opt_frame, text="Sort Now", command=self._run_sort)
        self.sort_btn.pack(side=tk.RIGHT)

        # ── Log area ────────────────────────────────────────────────────────
        log_frame = ttk.Frame(root, padding=(10, 0, 10, 4))
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_box = scrolledtext.ScrolledText(
            log_frame, state=tk.DISABLED, wrap=tk.WORD,
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", relief=tk.FLAT
        )
        self.log_box.pack(fill=tk.BOTH, expand=True)

        # ── Status bar ──────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready.")
        status_bar = ttk.Label(root, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W, padding=(6, 2))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _browse(self):
        chosen = filedialog.askdirectory(initialdir=self.folder_var.get(),
                                        title="Select folder to sort")
        if chosen:
            self.folder_var.set(chosen)

    def _log(self, message: str):
        """Append a line to the log box (thread-safe via after())."""
        def _append():
            self.log_box.configure(state=tk.NORMAL)
            self.log_box.insert(tk.END, message + "\n")
            self.log_box.see(tk.END)
            self.log_box.configure(state=tk.DISABLED)
        self.root.after(0, _append)

    def _run_sort(self):
        target_dir = Path(self.folder_var.get()).expanduser().resolve()
        dry_run = self.dry_run_var.get()

        # Clear log
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.delete("1.0", tk.END)
        self.log_box.configure(state=tk.DISABLED)

        self.sort_btn.configure(state=tk.DISABLED)
        mode_label = "DRY RUN — " if dry_run else ""
        self.status_var.set(f"{mode_label}Sorting {target_dir} …")
        self._log(f"Sorting: {target_dir}")
        if dry_run:
            self._log("(Dry run -- no files will actually be moved)\n")

        def worker():
            sort_folder(target_dir, dry_run=dry_run, log=self._log)
            def done():
                self.sort_btn.configure(state=tk.NORMAL)
                self.status_var.set("Done.")
            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()


def launch_gui(initial_dry_run: bool = False):
    root = tk.Tk()
    SortApp(root, initial_dry_run=initial_dry_run)
    root.mainloop()


if __name__ == "__main__":
    main()
