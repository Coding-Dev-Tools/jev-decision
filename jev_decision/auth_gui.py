"""Masked credential entry on the user's desktop; never returns the key to stdout."""
from __future__ import annotations


def main():
    import tkinter as tk
    from tkinter import messagebox

    from .credentials import save_api_key
    from .runtime import RuntimeConfig
    config = RuntimeConfig.load()
    root = tk.Tk()
    root.title("Jev secure key setup")
    root.geometry("510x230")
    root.resizable(False, False)
    tk.Label(root, text="Enter your TypeSafe API key", font=("Segoe UI", 13)).pack(pady=(20, 6))
    tk.Label(root, text="Stored with Windows user-bound encryption.\nThe key is not sent to Codex or written in harness settings.", font=("Segoe UI", 10)).pack()
    secret = tk.StringVar()
    entry = tk.Entry(root, textvariable=secret, show="*", width=55)
    entry.pack(pady=14)
    saved = [False]
    def submit():
        value = secret.get().strip()
        try:
            save_api_key(value, config)
        except Exception:
            value = ""
            messagebox.showerror("Key not saved", "Unable to save the key. Check that it is nonempty and Windows protection is available.")
            return
        value = ""
        secret.set("")
        saved[0] = True
        root.destroy()
    tk.Button(root, text="Encrypt and save", command=submit).pack()
    root.bind("<Return>", lambda event: submit())
    entry.focus_set()
    root.mainloop()
    return 0 if saved[0] else 2

if __name__ == "__main__":
    raise SystemExit(main())
