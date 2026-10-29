#!/usr/bin/python3
"""First-run GUI bootstrap for the Debian alpha; never modify system Python."""
import fcntl
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

PAYLOAD = Path(__file__).resolve().parent


def runtime_path(version):
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))
    return data/'runespeak-lite'/'runtimes'/f'{version}-py{sys.version_info.major}.{sys.version_info.minor}'


def install_runtime(runtime, wheel, log):
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime/'.setup.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (runtime/'.ready').exists():
            return
        subprocess.run([sys.executable, '-m', 'venv', str(runtime)], stdout=log, stderr=subprocess.STDOUT, check=True)
        python = str(runtime/'bin/python')
        subprocess.run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes', '-r', str(PAYLOAD/'requirements.txt')], stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run([python, '-m', 'pip', 'install', '--no-deps', str(wheel)], stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run([python, '-c', 'import tkinter, ptts, trafilatura, tkinterdnd2; from runespeak.app import App'], stdout=log, stderr=subprocess.STDOUT, check=True)
        (runtime/'.ready').write_text('ready\n')


def main():
    info = json.loads((PAYLOAD/'app.json').read_text())
    runtime = runtime_path(info['version'])
    python = runtime/'bin/python'
    if not (runtime/'.ready').exists():
        root = tk.Tk(className='Runespeaklite')
        root.title('Awakening Runespeak')
        root.geometry('540x230')
        root.configure(bg='#ead7ad')
        icon = tk.PhotoImage(file=str(PAYLOAD/'icon.png'))
        root.iconphoto(True, icon)
        tk.Label(root, text='Preparing your speaking tome', font=('serif', 20), bg='#ead7ad').pack(pady=16)
        status = tk.StringVar(value='Downloading the locked app dependencies.\nFirst launch needs internet; this may take several minutes.')
        tk.Label(root, textvariable=status, bg='#ead7ad', wraplength=500).pack(padx=15)
        progress = ttk.Progressbar(root, mode='indeterminate')
        progress.pack(fill='x', padx=24, pady=16)
        progress.start()
        events = queue.Queue()
        state = Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'runespeak-lite'
        state.mkdir(parents=True, exist_ok=True)
        log_path = state/'setup.log'

        def work():
            try:
                with log_path.open('w') as log:
                    install_runtime(runtime, PAYLOAD/info['wheel'], log)
                events.put(None)
            except Exception as exc:
                events.put(str(exc))

        def poll():
            try:
                error = events.get_nowait()
            except queue.Empty:
                root.after(150, poll)
                return
            progress.stop()
            if error is None:
                root.destroy()
            else:
                status.set(f'Setup could not finish. Close and reopen to retry.\nDetails: {log_path}')
                root.protocol('WM_DELETE_WINDOW', root.destroy)

        # Keep the setup window open while pip is running so installs cannot be
        # abandoned halfway through by accidentally closing the progress dialog.
        root.protocol('WM_DELETE_WINDOW', lambda: status.set('Setup is still running. Please wait for it to finish.'))
        threading.Thread(target=work, daemon=True).start()
        root.after(150, poll)
        root.mainloop()
        if not (runtime/'.ready').exists():
            return 1
    os.execv(str(python), [str(python), '-m', 'runespeak.app', *sys.argv[1:]])


if __name__ == '__main__':
    sys.exit(main())
