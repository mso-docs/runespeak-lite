"""The speaking tome: scalable Canvas ornament and native, readable controls."""
import math
from types import SimpleNamespace
import tkinter as tk
from tkinter import font, ttk

INK = '#392c22'
PAPER = '#ead7ad'
LEATHER = '#261b19'
GOLD = '#c7a565'


def configure_theme(root):
    families = set(font.families(root))
    serif = next((name for name in ('Bitstream Charter', 'Palatino', 'DejaVu Serif') if name in families), 'serif')
    # Use normal point fonts so Tk and the compositor honor desktop DPI.
    # Do not shrink fonts based on XWayland's reported pixel metrics.
    def size(pixels):
        return max(10, round(pixels * 0.85))
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('Tome.TFrame', background=PAPER)
    style.configure('Tome.TNotebook', background=PAPER, borderwidth=0)
    style.configure('Tome.TNotebook.Tab', background='#d4bb8b', foreground=INK, padding=(12, 7), font=(serif, size(13)))
    style.map('Tome.TNotebook.Tab', background=[('selected', '#f2e3c3'), ('active', '#e8d3ab')])
    style.configure('Seal.TButton', background=PAPER, borderwidth=0, padding=3)
    style.map('Seal.TButton', background=[('active', '#f4e5c4'), ('pressed', '#c7ad7b'), ('disabled', PAPER)])
    style.configure('Timer.TLabel', background=PAPER, foreground=INK, font=(serif, size(18)))
    style.configure('Tome.TCheckbutton', background=PAPER, foreground=INK, font=(serif, size(13)))
    style.map('Tome.TCheckbutton', background=[('active', PAPER)])
    style.configure('Tome.TLabel', background=PAPER, foreground=INK, font=(serif, size(15)))
    style.configure('Chapter.TLabel', background=PAPER, foreground='#795333', font=(serif, size(11), 'bold'))
    style.configure('Tome.TButton', background='#dcc395', foreground=INK, bordercolor='#a2804b', lightcolor='#eedbb6', darkcolor='#aa8655', padding=(7, 8), font=(serif, size(14)))
    style.map('Tome.TButton', background=[('disabled', '#e1cfac'), ('pressed', '#b99b67'), ('active', '#f3e3c1')], foreground=[('disabled', '#8c7b61')])
    style.configure('Speak.TButton', background='#354b3e', foreground='#fff1cd', bordercolor='#26392e', padding=(7, 8), font=(serif, size(14), 'bold'))
    style.map('Speak.TButton', background=[('disabled', '#737e69'), ('pressed', '#23392d'), ('active', '#49654f')], foreground=[('disabled', '#d4d5b8')])
    style.configure('Tome.TEntry', fieldbackground='#f5e7c9', foreground=INK, insertcolor=INK, bordercolor='#b99a65', padding=6, font=(serif, size(15)))
    style.configure('Tome.Vertical.TScrollbar', background='#c8ac78', troughcolor='#dfc697', bordercolor='#b4945e', arrowcolor=INK)
    style.configure('Tome.Horizontal.TProgressbar', background='#62755a', troughcolor='#d6bc8c', bordercolor='#b89760', lightcolor='#81966f', darkcolor='#465b40', thickness=5)
    return serif, size


class TomeSurface(tk.Canvas):
    def __init__(self, root, serif, size):
        super().__init__(root, background=LEATHER, highlightthickness=0)
        self.serif = serif
        self.size = size
        self.pack(fill='both', expand=True)
        self.content = ttk.Frame(self, style='Tome.TFrame')
        self.window = self.create_window(52, 162, anchor='nw', window=self.content)
        self.bind('<Configure>', self.redraw)
        self.reflow_pending = False
        self.content.bind('<Configure>', self.request_reflow)

    def request_reflow(self, event=None):
        if not self.reflow_pending:
            self.reflow_pending = True
            self.after_idle(self.reflow)

    def reflow(self):
        self.reflow_pending = False
        self.redraw(SimpleNamespace(width=self.winfo_width(), height=self.winfo_height()))

    def redraw(self, event):
        w, h = event.width, event.height
        self.delete('ornament')
        def rect(*args, **kwargs):
            return self.create_rectangle(*args, tags='ornament', **kwargs)
        def line(*args, **kwargs):
            return self.create_line(*args, tags='ornament', **kwargs)
        def text(*args, **kwargs):
            return self.create_text(*args, tags='ornament', **kwargs)
        # Leather cover, blind tooling, brass corner plates and a stitched spine.
        rect(9, 9, w-9, h-9, outline='#71533a', width=2)
        rect(16, 16, w-16, h-16, outline='#49342a')
        for x in (23, w-23):
            for y in range(35, h-24, 13):
                line(x, y, x, y+4, fill='#796044')
        for x, direction in ((12, 1), (w-12, -1)):
            for y, vertical in ((12, 1), (h-12, -1)):
                self.create_polygon(x,y,x+direction*34,y,x,y+vertical*34, fill='#987342', outline=GOLD, tags='ornament')
                self.create_oval(x+direction*9-2,y+vertical*9-2,x+direction*9+2,y+vertical*9+2,fill='#ecd39a',outline='',tags='ornament')
        overline = text(48, 29, anchor='nw', width=max(1, w-112), text='THE ADVENTURER’S COMPANION  /  VOL. I', fill=GOLD, font=(self.serif, self.size(10)))
        title_top = self.bbox(overline)[3] + 7
        title_font = font.Font(self, family=self.serif, size=self.size(42), weight='bold')
        available = w-210 if w > 730 else w-100
        while title_font.measure('Runespeak') > available and title_font.cget('size') > 16:
            title_font.configure(size=title_font.cget('size')-1)
        self.title_font = title_font
        title = text(46, title_top, anchor='nw', text='Runespeak', fill='#f1dbab', font=title_font)
        subtitle = text(49, self.bbox(title)[3]+8, anchor='nw', width=max(1,w-112), text='A speaking tome for weary adventurers.', fill='#c8b38f', font=(self.serif, self.size(15), 'italic'))
        page_top = max(143, self.bbox(subtitle)[3] + 18)
        self.header_items = (overline, title, subtitle)
        # A geometric arcane seal, drawn locally rather than loaded as a bitmap.
        if w > 730:
            cx, cy = w-105, 78
            for radius in (39, 33):
                self.create_oval(cx-radius,cy-radius,cx+radius,cy+radius,outline=GOLD,tags='ornament')
            points = []
            for i in (0, 2, 4, 1, 3, 0):
                angle = -math.pi/2 + i*2*math.pi/5
                points.extend((cx+25*math.cos(angle),cy+25*math.sin(angle)))
            line(*points, fill='#9a7c4d', width=1)
            text(cx,cy,text='R',fill='#efd6a2',font=(self.serif, self.size(21),'bold'))
        # Offset page edges and a warm gutter create the silhouette of a bound tome.
        rect(34, page_top+7, w-27, h-29, fill='#8e724d', outline='#5c432f')
        rect(31, page_top+4, w-30, h-32, fill='#d0b585', outline='#9c7d4e')
        rect(28, page_top, w-33, h-35, fill=PAPER, outline='#c3a46b')
        rect(29, page_top+1, 38, h-36, fill='#c5a36e', outline='')
        line(41,page_top+7,41,h-43,fill='#f9e6bd')
        line(47,page_top+10,w-51,page_top+10,fill='#ba985f')
        line(47,h-44,w-51,h-44,fill='#c4a56f')
        text(w/2,h-20,text='✦  WORDS INTO VOICE · A LITTLE EVERYDAY MAGIC  ✦',fill='#b99a67',font=(self.serif, self.size(9)))
        self.coords(self.window, 52, page_top+23)
        self.itemconfigure(self.window, width=max(1,w-112), height=max(1,h-page_top-77))
        self.tag_lower('ornament')
        # Reserve space for the editor as well as controls at desktop font sizes.
        root = self.winfo_toplevel()
        minimum_w = min(root.winfo_screenwidth()-60, max(960, self.content.winfo_reqwidth()+112))
        minimum_h = min(root.winfo_screenheight()-100, max(800, page_top+77+self.content.winfo_reqheight()))
        if root.minsize() != (minimum_w, minimum_h):
            root.minsize(minimum_w, minimum_h)
