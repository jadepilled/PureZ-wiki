r"""Debinarise every ODOL .p3d under a folder with DeODOL, falling back to the older build.

    python pipeline/deodol.py                      # uses ..\PureZ-render next to the wiki repo
    python pipeline/deodol.py F:\PureZ\PureZ-render --exe C:\path\Deodol54.exe --fallback C:\path\DeODOL53.exe

For each .p3d whose header is ODOL: run the primary DeODOL on it; if the file is not MLOD afterwards
(the tool crashed, timed out or left it untouched) run the fallback build. A file that is already MLOD is
skipped, so the script can be re-run. Results go to deodol.log next to the folder; failures are listed
at the end so they can be retried by hand. Use --dry-run to only count.
"""
import argparse, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.normpath(os.path.join(HERE, "..", "..", "PureZ-render"))   # F:\PureZ\PureZ-render next to F:\PureZ\PureZ-wiki
EXE = r"C:\Users\Jade\Desktop\Deodol54.exe"
FALLBACK = r"C:\Users\Jade\Desktop\DeODOL53.exe"


def header(path):
    try:
        with open(path, "rb") as f:
            return f.read(4)
    except OSError:
        return b""


def run(exe, path, timeout, settle, log=None):
    """Run a converter on one file. True when the file is MLOD afterwards (in place, or written as a
    sibling file that is then moved over the original, the ODOL kept as <name>.odol)."""
    folder = os.path.dirname(path)
    before = snapshot(folder)
    try:
        r = subprocess.run([exe, path], timeout=timeout, cwd=folder, capture_output=True, text=True, errors="replace")
        if log is not None and (r.stdout or r.stderr):
            log.write((r.stdout + r.stderr).strip() + "\n")
    except subprocess.TimeoutExpired:
        return False
    except OSError as e:
        print(f"  cannot run {exe}: {e}", file=sys.stderr)
        return False
    end = time.time() + settle    # some builds return before the file is written
    while time.time() < end:
        if header(path) == b"MLOD":
            return True
        time.sleep(0.25)
    if header(path) == b"MLOD":
        return True
    # written next to the original under another name?
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    for f, (size, mtime) in snapshot(folder).items():
        if before.get(f) != (size, mtime) and f.lower() != os.path.basename(path).lower() and stem in f.lower():
            full = os.path.join(folder, f)
            if header(full) == b"MLOD":
                os.replace(path, path[:-4] + ".odol")
                os.replace(full, path)
                return True
    return False


def snapshot(folder):
    out = {}
    for f in os.listdir(folder):
        try:
            st = os.stat(os.path.join(folder, f))
            out[f] = (st.st_size, st.st_mtime)
        except OSError:
            pass
    return out


def probe(a, odol):
    """Show exactly what the converter does with one file, in a scratch copy."""
    import shutil, tempfile
    print(f"exe: {a.exe}  ({os.path.getsize(a.exe) if os.path.exists(a.exe) else 'missing'} bytes)")
    print("--- running with no arguments (usage text, if any)")
    try:
        r = subprocess.run([a.exe], timeout=15, capture_output=True, text=True, errors="replace")
        print(f"exit {r.returncode}\n{(r.stdout + r.stderr).strip()[:2000]}")
    except Exception as e:
        print(f"  {e}")
    src = odol[0]
    work = tempfile.mkdtemp(prefix="deodol_probe_")
    copy = os.path.join(work, os.path.basename(src))
    shutil.copy2(src, copy)
    for label, cmd in (("one argument", [a.exe, copy]), ("input and output", [a.exe, copy, copy[:-4] + "_mlod.p3d"])):
        print(f"--- {label}: {' '.join(cmd)}")
        before = {d: snapshot(d) for d in (work, os.path.dirname(a.exe), os.getcwd())}
        t0 = time.time()
        try:
            r = subprocess.run(cmd, timeout=a.timeout, cwd=work, capture_output=True, text=True, errors="replace")
            print(f"exit {r.returncode} after {time.time() - t0:.1f}s\n{(r.stdout + r.stderr).strip()[:2000]}")
        except subprocess.TimeoutExpired:
            print("  timed out")
        except OSError as e:
            print(f"  cannot run: {e}")
        time.sleep(a.settle)
        print(f"  {os.path.basename(copy)} header now: {header(copy)!r}")
        for d, snap in before.items():
            for f, st in snapshot(d).items():
                if snap.get(f) != st:
                    full = os.path.join(d, f)
                    print(f"  new or changed: {full}  header {header(full)!r}  {os.path.getsize(full)} bytes")
    print(f"scratch folder kept at {work}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=DEFAULT_ROOT, help=f"folder to walk, the render set from dayz_prune.py (default: {DEFAULT_ROOT})")
    ap.add_argument("--exe", default=EXE)
    ap.add_argument("--fallback", default=FALLBACK)
    ap.add_argument("--timeout", type=int, default=120, help="seconds per file per converter")
    ap.add_argument("--settle", type=float, default=3, help="seconds to wait for the output after the converter returns")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true", help="try the converter on one file in a scratch folder and show everything it does")
    a = ap.parse_args()

    if not os.path.isdir(a.root):
        sys.exit(f"render folder not found: {a.root}\nPass it as the first argument, e.g. python deodol.py F:\\PureZ\\PureZ-render")
    for exe in (a.exe, a.fallback):
        if not os.path.exists(exe):
            print(f"note: {exe} not found" + (" (no fallback)" if exe == a.fallback else ""))
    if not os.path.exists(a.exe):
        sys.exit("--exe must point at Deodol54.exe")
    files = [os.path.join(d, f) for d, _, fs in os.walk(a.root) for f in fs if f.lower().endswith(".p3d")]
    odol = [f for f in files if header(f) == b"ODOL"]
    print(f"{len(files)} models, {len(odol)} still ODOL")
    if a.probe and odol:
        return probe(a, odol)
    if a.dry_run or not odol:
        return
    log = open(os.path.join(a.root, "deodol.log"), "a", encoding="utf-8")
    done, fell_back, failed = 0, 0, []
    for i, path in enumerate(odol, 1):
        rel = os.path.relpath(path, a.root)
        if run(a.exe, path, a.timeout, a.settle, log):
            done += 1
            log.write(f"ok  {rel}\n")
        elif os.path.exists(a.fallback) and run(a.fallback, path, a.timeout, a.settle, log):
            done += 1
            fell_back += 1
            log.write(f"ok  {rel}  (fallback)\n")
        else:
            failed.append(rel)
            log.write(f"FAIL {rel}\n")
        if i % 25 == 0 or i == len(odol):
            print(f"  {i}/{len(odol)}  converted {done}, via fallback {fell_back}, failed {len(failed)}")
        if i == 10 and done == 0:
            print("  nothing converted yet: stopping. Run with --probe to see what the converter does, and check deodol.log for its messages.")
            break
        log.flush()
    log.close()
    if failed:
        print(f"{len(failed)} files stayed ODOL (listed in deodol.log):")
        for f in failed[:30]:
            print("  " + f)
    print("Next: python pipeline/layout.py --render " + a.root)


if __name__ == "__main__":
    main()
