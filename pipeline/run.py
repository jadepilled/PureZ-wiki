"""Convert and render everything in one go, with a progress bar.

    python pipeline/run.py --render F:/PureZ/PureZ-render [--blender "C:/Program Files/.../blender.exe"] [--only AKM]

Stage 1 runs layout.py (game models -> pipeline/work, one folder per wiki item), stage 2 runs Blender on the
work tree (resumable: items that already have an image are skipped). Both stages stream their progress here;
the full logs are pipeline/work/layout.log and pipeline/work/render.log. Any extra arguments after `--`
go to blender_render.py (for example `-- --engine CYCLES --gpu`).
"""
import argparse, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BLENDER_DEFAULTS = [r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
                    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe", "blender"]


# ---------------------------------------------------------------- console
def enable_ansi():
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass
    return sys.stdout.isatty()


ANSI = enable_ansi()


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if ANSI else text


def fmt_time(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m {sec % 60:02d}s"


class Bar:
    """One-line progress bar: [stage] ████████░░░░ 123/2028 · ok 120 · failed 3 · 12.3/s · left 4m 10s · current item."""

    def __init__(self, stage, total):
        self.stage, self.total, self.n, self.start = stage, max(total, 1), 0, time.time()
        self.counts, self.current, self.last_draw = {}, "", 0.0

    def step(self, status, item):
        self.n += 1
        self.counts[status] = self.counts.get(status, 0) + 1
        self.current = item
        self.draw()

    def draw(self, final=False):
        now = time.time()
        if not final and now - self.last_draw < 0.1:
            return
        self.last_draw = now
        cols = shutil.get_terminal_size((100, 20)).columns
        frac = min(1.0, self.n / self.total)
        rate = self.n / max(now - self.start, 1e-6)
        left = (self.total - self.n) / rate if rate and self.n else 0
        tail = " · ".join(f"{paint(k, '32' if k in ('ok', 'rendered', 'skipped') else '31' if k in ('failed', 'missing') else '33')} {v}"
                          for k, v in sorted(self.counts.items()))
        info = f" {self.n}/{self.total} · {tail} · {rate:.1f}/s · left {fmt_time(left)} "
        width = max(10, min(40, cols - len(re.sub(r'\033\[[0-9;]*m', '', info)) - len(self.stage) - 20))
        filled = int(width * frac)
        bar = paint("█" * filled, "36") + paint("░" * (width - filled), "90")
        cur = self.current[:max(0, cols - width - len(re.sub(r'\033\[[0-9;]*m', '', info)) - len(self.stage) - 3)]
        line = f"\r{paint(self.stage, '1')} {bar}{info}{paint(cur, '90')}"
        sys.stdout.write("\r\033[K" + line if ANSI else "\r" + line.ljust(cols - 1))
        sys.stdout.flush()
        if final:
            sys.stdout.write("\n")


def run_stage(cmd, stage, total_rx, line_rx, log_path, cwd=None):
    """Run a subprocess, parse its lines for the total and per-item results, draw the bar, keep the full log."""
    bar = None
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", cwd=cwd, bufsize=1)
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            log.write(line + "\n")
            m = total_rx.search(line)
            if m and bar is None:
                bar = Bar(stage, int(m.group(1)))
                bar.draw()
                continue
            m = line_rx.search(line)
            if m and bar is not None:
                bar.step(m.group("status") or "rendered", m.group("item"))
            elif bar is None and line.strip() and not line.startswith(("Blender", "Read", "Color management")):
                print(paint(line, "90"))
        code = proc.wait()
    if bar:
        bar.draw(final=True)
    return code, bar


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--render", required=True, help="render folder from dayz_trim.py (contains DZ/)")
    ap.add_argument("--blender", help="Blender executable (default: Blender 5.1, then 4.2, then PATH)")
    ap.add_argument("--work", default=os.path.join(HERE, "work"))
    ap.add_argument("--out", default=os.path.join(ROOT, "media"))
    ap.add_argument("--only", help="one class, or any class containing this text")
    ap.add_argument("--all", action="store_true", help="every config class with a model, not just wiki items")
    ap.add_argument("--skip-convert", action="store_true", help="work tree is up to date; only render")
    ap.add_argument("--skip-render", action="store_true", help="only convert")
    ap.add_argument("--fresh", action="store_true", help="re-render items that already have an image")
    ap.add_argument("blender_args", nargs="*", help="arguments after -- go to blender_render.py")
    a = ap.parse_args()
    if not os.path.isdir(a.render):
        sys.exit(f"render folder not found: {a.render}  (in Git Bash write it with forward slashes: F:/PureZ/PureZ-render)")
    os.makedirs(a.work, exist_ok=True)
    t0 = time.time()
    print(paint(f"PureZ render pipeline · work {a.work} · out {a.out}", "1"))

    if not a.skip_convert:
        cmd = [sys.executable, os.path.join(HERE, "layout.py"), "--render", a.render, "--work", a.work] + (["--only", a.only] if a.only else []) + (["--all"] if a.all else [])
        code, bar = run_stage(cmd, "Convert", re.compile(r"^(\d+) classes to prepare"),
                              re.compile(r"^(?P<status>ok|missing|odol|failed)\s+(?P<item>.+)$"), os.path.join(a.work, "layout.log"))
        if code or not bar:
            sys.exit(f"convert stage failed (exit {code}); see {os.path.join(a.work, 'layout.log')}")
        if bar.counts.get("missing"):
            print(paint(f"  {bar.counts['missing']} models are not in the render folder (listed in layout.log)", "33"))

    if not a.skip_render:
        blender = a.blender or next((b for b in BLENDER_DEFAULTS if shutil.which(b) or os.path.exists(b)), None)
        if not blender or not (shutil.which(blender) or os.path.exists(blender)):
            sys.exit("Blender not found; pass --blender \"C:/Program Files/Blender Foundation/Blender 5.1/blender.exe\"")
        cmd = [blender, "-b", "--factory-startup", "-P", os.path.join(HERE, "blender_render.py"), "--",
               "--work", a.work, "--out", a.out] + ([] if a.fresh else ["--resume"]) + (["--only", a.only] if a.only else []) + a.blender_args
        code, bar = run_stage(cmd, "Render ", re.compile(r"^(\d+) models to render"),
                              re.compile(r"^\[\d+/\d+\] (?:(?P<status>FAILED|skipped) )?(?P<item>\S+)"), os.path.join(a.work, "render.log"))
        if code or not bar:
            sys.exit(f"render stage failed (exit {code}); see {os.path.join(a.work, 'render.log')}")
        rep = os.path.join(a.out, "renders", "report.json")
        if bar.counts.get("FAILED"):
            print(paint(f"  {bar.counts['FAILED']} renders failed; errors with tracebacks are in {rep}", "31"))

    print(paint(f"done in {fmt_time(time.time() - t0)}", "1"))


if __name__ == "__main__":
    main()
