"""Fade Note - Stealth edition with image support, hotkey toggle, and Gemini fact-checking.

Hotkeys:
  - Alt + C: Completely hide / reveal note (returns to set fade opacity)
  - Alt + G: Fact-check selected text/images (or current line) with Gemini
  - Ctrl + V: Paste text, clipboard images, or copied image files into the note

Requirements:
  pip install pillow google-genai keyboard
"""
import json
import os
import threading
import tkinter as tk
import uuid

# ---- GEMINI API KEY SETUP ----
# Paste your Gemini API key between the quotes below:
GEMINI_API_KEY = "AQ.Ab8RN6LrsSgKUD0QesDjGU6kxwDk15QwEA3VqbGF7G_l2kTaow"
GEMINI_MODEL = "gemini-3.8-flash"
MAX_IMAGES_PER_QUERY = 6

CFG = os.path.join(os.path.expanduser("~"), ".fade_note.json")
IMG_DIR = os.path.join(os.path.expanduser("~"), ".fade_note_images")
DEFAULT = {"text": "", "content": None, "x": None, "y": 24, "w": 300, "h": 220, "alpha": 0.35}

# Stealth Dark Palette (subtle and low-contrast to minimize visibility)
PAPER = "#1e1e1e"
BAR   = "#2a2a2a"
INK   = "#b0b0b0"
ACCENT= "#3a3a3a"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

# Optional libraries
try:
    from PIL import Image, ImageGrab, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=GEMINI_API_KEY)
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False


def load():
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            return {**DEFAULT, **json.load(f)}
    except Exception:
        return dict(DEFAULT)


class FadeNote:
    def __init__(self):
        self.s = load()
        self.preview = False
        self.dirty = False
        self.hidden = False
        self.ticks = 0
        self.photos = {}  # image id -> PhotoImage (prevents garbage collection)

        r = self.root = tk.Tk()
        r.title("Fade Note")
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg=PAPER)

        x = self.s["x"]
        if x is None:
            x = r.winfo_screenwidth() - self.s["w"] - 24
        r.geometry(f"{self.s['w']}x{self.s['h']}+{x}+{self.s['y']}")

        # ---- Top Bar ----
        bar = tk.Frame(r, bg=BAR, height=26)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        self.lbl_status = tk.Label(bar, text="Fade", bg=BAR, fg=INK, font=("Segoe UI", 8))
        self.lbl_status.pack(side="left", padx=(8, 4))

        scale = tk.Scale(
            bar, from_=0.1, to=1.0, resolution=0.05, orient="horizontal",
            showvalue=False, length=75, bg=BAR, troughcolor=ACCENT,
            highlightthickness=0, bd=0, sliderlength=12,
            command=self.on_scale,
        )
        scale.set(self.s["alpha"])
        scale.pack(side="left", pady=2)
        scale.bind("<ButtonPress-1>", lambda e: setattr(self, "preview", True))
        scale.bind("<ButtonRelease-1>", lambda e: setattr(self, "preview", False))

        close = tk.Label(bar, text="×", bg=BAR, fg=INK, font=("Segoe UI", 12), cursor="hand2")
        close.pack(side="right", padx=6)
        close.bind("<Button-1>", lambda e: self.quit())

        ai_btn = tk.Label(bar, text="●", bg=BAR, fg=INK, font=("Segoe UI", 10), cursor="hand2")
        ai_btn.pack(side="right", padx=4)
        ai_btn.bind("<Button-1>", lambda e: self.ask_gemini())

        for w in (bar, self.lbl_status):
            w.bind("<ButtonPress-1>", self.start_move)
            w.bind("<B1-Motion>", self.do_move)
            w.bind("<ButtonRelease-1>", lambda e: self.save())

        # ---- Text Area ----
        self.text = tk.Text(
            r, bg=PAPER, fg=INK, bd=0, relief="flat", wrap="word",
            padx=10, pady=8, font=("Segoe UI", 10), insertbackground=INK,
            undo=True, highlightthickness=0,
        )
        self.text.pack(side="top", fill="both", expand=True)
        self.restore_content()
        self.text.bind("<<Modified>>", self.on_modified)
        for seq in ("<Control-v>", "<Control-V>", "<Shift-Insert>"):
            self.text.bind(seq, self.on_paste)
        self.text.edit_modified(False)
        self.text.edit_reset()

        # ---- Resize Grip ----
        grip = tk.Label(r, text="◢", bg=PAPER, fg=ACCENT, font=("Segoe UI", 8), cursor="size_nw_se")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<B1-Motion>", self.do_resize)
        grip.bind("<ButtonRelease-1>", lambda e: self.save())

        # ---- Window-Level Hotkey Fallbacks ----
        r.bind_all("<Alt-c>", lambda e: self.toggle_visibility())
        r.bind_all("<Alt-C>", lambda e: self.toggle_visibility())
        r.bind_all("<Alt-g>", lambda e: self.ask_gemini())
        r.bind_all("<Alt-G>", lambda e: self.ask_gemini())

        # ---- Global System-Wide Hotkeys ----
        if KEYBOARD_AVAILABLE:
            try:
                keyboard.add_hotkey("alt+c", lambda: self.root.after(0, self.toggle_visibility))
                keyboard.add_hotkey("alt+g", lambda: self.root.after(0, self.ask_gemini))
            except Exception:
                pass

        r.attributes("-alpha", self.s["alpha"])
        r.protocol("WM_DELETE_WINDOW", self.quit)
        self.tick()

    # ---- Visibility & Fading ----
    def toggle_visibility(self):
        self.hidden = not self.hidden
        if self.hidden:
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-alpha", self.s["alpha"])

    def on_scale(self, value):
        self.s["alpha"] = float(value)
        self.dirty = True

    def on_modified(self, _event):
        if self.text.edit_modified():
            self.dirty = True
            self.text.edit_modified(False)

    def start_move(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def do_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def do_resize(self, e):
        w = max(180, e.x_root - self.root.winfo_x())
        h = max(110, e.y_root - self.root.winfo_y())
        self.root.geometry(f"{w}x{h}")

    def pointer_inside(self):
        r = self.root
        px, py = r.winfo_pointerxy()
        return (r.winfo_rootx() <= px < r.winfo_rootx() + r.winfo_width()
                and r.winfo_rooty() <= py < r.winfo_rooty() + r.winfo_height())

    def tick(self):
        if not self.hidden:
            active = self.pointer_inside() or self.root.focus_get() is self.text
            solid = active and not self.preview
            self.root.attributes("-alpha", 0.6 if solid else self.s["alpha"])

        self.ticks += 1
        if self.dirty and self.ticks % 20 == 0:
            self.save()
        self.root.after(150, self.tick)

    def flash_status(self, msg, ms=2000):
        self.lbl_status.config(text=msg)
        self.root.after(ms, lambda: self.lbl_status.config(text="Fade"))

    # ---- Image Helpers ----
    @staticmethod
    def _img_path(iid):
        return os.path.join(IMG_DIR, f"{iid}.png")

    def _current_width(self):
        w = self.root.winfo_width()
        return w if w > 1 else self.s["w"]

    def _fit(self, img):
        """Shrink an image to fit the note's width (never enlarges)."""
        max_w = max(120, self._current_width() - 36)
        w, h = img.size
        if w > max_w:
            img = img.resize((max_w, max(1, int(h * max_w / float(w)))), Image.Resampling.LANCZOS)
        return img

    def _embed_image(self, img, iid, index=tk.INSERT):
        photo = ImageTk.PhotoImage(self._fit(img))
        self.photos[iid] = photo
        self.text.image_create(index, image=photo, name=iid)

    # ---- Image Paste Support ----
    def _clipboard_images(self):
        """Return a list of PIL images from the clipboard (bitmap or copied files)."""
        data = ImageGrab.grabclipboard()
        images = []
        if isinstance(data, list):  # copied files (Explorer / Finder)
            for p in data:
                if isinstance(p, str) and p.lower().endswith(IMAGE_EXTS):
                    try:
                        im = Image.open(p)
                        im.load()
                        images.append(im)
                    except Exception:
                        pass
        elif data is not None and hasattr(data, "size"):  # raw bitmap
            images.append(data)
        return images

    def on_paste(self, event=None):
        if not PIL_AVAILABLE:
            self.flash_status("pip install pillow")
            return None  # fall through to normal text paste

        try:
            images = self._clipboard_images()
        except Exception:
            images = []

        if not images:
            return None  # no image on clipboard -> normal text paste

        os.makedirs(IMG_DIR, exist_ok=True)
        for img in images:
            try:
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                iid = uuid.uuid4().hex
                img.save(self._img_path(iid), "PNG")  # keep the full-size original on disk
                self._embed_image(img, iid)
                self.text.insert(tk.INSERT, "\n")
            except Exception:
                self.flash_status("Paste failed")
        self.dirty = True
        return "break"

    # ---- Content Serialization (text + images) ----
    def serialize(self):
        segs = []
        for key, value, _idx in self.text.dump("1.0", "end-1c", text=True, image=True):
            if key == "text":
                if segs and segs[-1][0] == "text":
                    segs[-1][1] += value
                else:
                    segs.append(["text", value])
            elif key == "image":
                segs.append(["image", value])
        return segs

    def restore_content(self):
        content = self.s.get("content")
        if not content:
            self.text.insert("1.0", self.s.get("text", ""))
            return
        for kind, value in content:
            if kind == "text":
                self.text.insert("end-1c", value)
            elif kind == "image":
                path = self._img_path(value)
                if PIL_AVAILABLE and os.path.exists(path):
                    try:
                        img = Image.open(path)
                        img.load()
                        self._embed_image(img, value, index="end-1c")
                        continue
                    except Exception:
                        pass
                self.text.insert("end-1c", "[image missing]")

    def _extract(self, start, end):
        """Return (plain_text, [image_ids]) for a text-widget range."""
        parts, ids = [], []
        for key, value, _idx in self.text.dump(start, end, text=True, image=True):
            if key == "text":
                parts.append(value)
            elif key == "image":
                ids.append(value)
        return "".join(parts).strip(), ids

    # ---- Gemini Fact-Checking ----
    def ask_gemini(self):
        if (not GEMINI_AVAILABLE or not GEMINI_API_KEY
                or GEMINI_API_KEY in ("the key", "PUT_YOUR_KEY_HERE")):
            self.flash_status("No Key")
            return

        # 1) selection, 2) current line, 3) whole note
        try:
            start, end = self.text.index(tk.SEL_FIRST), self.text.index(tk.SEL_LAST)
        except tk.TclError:
            start, end = "insert linestart", "insert lineend"

        query, image_ids = self._extract(start, end)
        if not query and not image_ids:
            query, image_ids = self._extract("1.0", "end-1c")
        if not query and not image_ids:
            return

        image_ids = image_ids[:MAX_IMAGES_PER_QUERY]
        self.lbl_status.config(text="Checking...")
        threading.Thread(target=self._query_worker, args=(query, image_ids), daemon=True).start()

    def _query_worker(self, query, image_ids):
        try:
            contents = []
            for iid in image_ids:
                path = self._img_path(iid)
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        contents.append(types.Part.from_bytes(data=f.read(), mime_type="image/png"))

            prompt = (
                "Give ONLY the final answer, as short as possible, with no explanation. "
                "If it is multiple choice, reply with just the letter and option text. "
                "If it is yes/no, reply Yes or No. "
                "If it is a calculation, reply with just the result."
            )
            if query:
                prompt += "\n\n" + query
            else:
                prompt += "\n\nThe question is in the attached image(s)."
            contents.append(prompt)

            response = client.models.generate_content(model=GEMINI_MODEL, contents=contents)
            answer = (response.text or "(no answer)").strip()
            msg = f"💡 AI: {answer}\n{'─'*28}\n\n"
        except Exception as e:
            msg = f"⚠️ Error: {e}\n\n"  # capture now: `e` is deleted after this block

        self.root.after(0, lambda m=msg: self._insert_top(m))
        self.root.after(0, lambda: self.lbl_status.config(text="Fade"))

    def _insert_top(self, text):
        self.text.insert("1.0", text)
        self.dirty = True

    # ---- State & Lifecycle ----
    def save(self):
        r = self.root
        content = self.serialize()
        self.s.update(
            content=content,
            text="".join(v for k, v in content if k == "text"),  # plain-text backup
            x=r.winfo_x(), y=r.winfo_y(),
            w=r.winfo_width(), h=r.winfo_height(),
        )
        try:
            with open(CFG, "w", encoding="utf-8") as f:
                json.dump(self.s, f)
            self.dirty = False
        except OSError:
            pass

    def cleanup_images(self):
        """Delete saved image files that are no longer in the note."""
        try:
            used = {v for k, v in self.serialize() if k == "image"}
            for name in os.listdir(IMG_DIR):
                if name.endswith(".png") and name[:-4] not in used:
                    os.remove(os.path.join(IMG_DIR, name))
        except OSError:
            pass

    def quit(self):
        self.save()
        self.cleanup_images()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FadeNote().run()