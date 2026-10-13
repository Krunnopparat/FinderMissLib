"""ติดตั้ง findermiss จากโฟลเดอร์นี้ แสดงผลสไตล์ bun install

    python install.py           # numpy, pandas
    python install.py --plot    # + matplotlib สำหรับ plot()
"""
import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
BOLD, DIM, RED, GREEN = "1", "2", "31", "32"


def style(text, code):
    # ถ้า output ถูก pipe ไปไฟล์/log รหัสสีจะกลายเป็นขยะในไฟล์
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else str(text)


def run(cmd, failure, cwd=None, spinner=None):
    """รันคำสั่งแล้วคืน stdout ถ้าพังให้พิมพ์ error + output ของคำสั่งแล้วออกด้วย exit code 1"""
    done = threading.Event()

    def spin():
        for frame in itertools.cycle(SPINNER_FRAMES):
            print(f"\r{style(frame, DIM)} {spinner}", end="", flush=True)
            if done.wait(0.08):
                return

    spinning = spinner is not None and sys.stdout.isatty()
    if spinning:
        thread = threading.Thread(target=spin)
        thread.start()
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    done.set()
    if spinning:
        thread.join()
        print("\r\033[K", end="")
    if result.returncode != 0:
        print(f"{style('error', RED)}: {failure}\n")
        print(result.stdout + result.stderr)
        sys.exit(1)
    return result.stdout.strip()


def installed_packages(report_path):
    """อ่าน JSON จาก pip install --report แล้วคืน [(ชื่อ, เวอร์ชัน)] ที่ติดตั้งจริง เรียงตามชื่อ"""
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
    packages = [(item["metadata"]["name"], item["metadata"]["version"]) for item in report["install"]]
    return sorted(packages, key=lambda package: package[0].lower())


def enable_ansi_on_windows_console():
    # console ของ Windows แบบเก่า (cmd/PowerShell ที่ไม่ใช่ Windows Terminal) ปิดการแปลรหัสสีไว้เป็นค่าเริ่มต้น
    import ctypes
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    kernel32 = ctypes.windll.kernel32
    stdout_handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(stdout_handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)


def format_duration(seconds):
    return f"{seconds:.2f}s" if seconds >= 1 else f"{seconds * 1000:.2f}ms"


def main():
    parser = argparse.ArgumentParser(description="Install findermiss from this folder.")
    parser.add_argument("--plot", action="store_true", help="also install matplotlib for plot()")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    if os.name == "nt" and sys.stdout.isatty():
        enable_ansi_on_windows_console()
    with open(os.path.join(HERE, "findermiss.py"), encoding="utf-8") as f:
        version = re.search(r'^__version__ = "(.+)"', f.read(), re.M).group(1)
    python = sys.version.split()[0]
    print(f"{style('findermiss install', BOLD)} {style(f'v{version} (python {python})', DIM)}\n")

    run([sys.executable, "-m", "pip", "--version"], "pip is not available for this Python")
    target = HERE + "[plot]" if args.plot else HERE
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        report = os.path.join(tmp, "report.json")
        run([sys.executable, "-m", "pip", "install", "--quiet", "--report", report, target],
            "pip install failed", spinner="Resolving and installing...")
        packages = installed_packages(report)
    elapsed = time.perf_counter() - started
    # รันนอกโฟลเดอร์นี้ ไม่อย่างนั้น import จะเจอ findermiss.py ต้นฉบับแทนตัวที่เพิ่งติดตั้ง
    run([sys.executable, "-c", "import findermiss"],
        "findermiss was installed but cannot be imported", cwd=tempfile.gettempdir())

    for name, package_version in packages:
        print(f"{style('+', GREEN)} {name}{style('@' + package_version, DIM)}")
    noun = "package" if len(packages) == 1 else "packages"
    print(f"\n {style(len(packages), GREEN)} {noun} installed {style(f'[{format_duration(elapsed)}]', DIM)}")


if __name__ == "__main__":
    main()
