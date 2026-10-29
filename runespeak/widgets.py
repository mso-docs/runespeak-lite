"""Small native helpers for the tome's icon controls."""
import tkinter as tk


class Hint:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<FocusIn>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<FocusOut>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def show(self, event=None):
        if self.window:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+self.widget.winfo_height()+5}')
        text = self.text() if callable(self.text) else self.text
        tk.Label(self.window, text=text, background='#392c22', foreground='#f5e3bd', padx=10, pady=6).pack()

    def hide(self, event=None):
        if self.window:
            window, self.window = self.window, None
            window.destroy()
