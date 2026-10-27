"""Construit JS8 Carte pour Windows :
    python packaging/build.py               -> packaging/dist/JS8Carte/JS8Carte.exe
    python packaging/build.py --installer   -> + packaging/dist/JS8Carte-Setup-<version>.exe (Inno Setup 6)

Prérequis : pip install pyinstaller pywebview pillow  (+ Inno Setup 6 pour --installer)
"""

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(HERE, "dist")
WORK = os.path.join(HERE, "build")


def make_icon():
    from PIL import Image, ImageDraw, ImageFont
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=44, fill=(11, 18, 32, 255))
    d.ellipse((40, 40, 216, 216), outline=(79, 195, 247, 255), width=14)
    d.line((128, 40, 128, 216), fill=(79, 195, 247, 160), width=6)
    d.line((40, 128, 216, 128), fill=(79, 195, 247, 160), width=6)
    d.ellipse((150, 70, 186, 106), fill=(255, 193, 7, 255))
    try:
        font = ImageFont.truetype("arialbd.ttf", 78)
    except OSError:
        font = ImageFont.load_default()
    d.text((128, 168), "JS8", font=font, fill="white", anchor="mm", stroke_width=6, stroke_fill=(11, 18, 32))
    path = os.path.join(HERE, "icon.ico")
    img.save(path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    return path


def app_version():
    with open(os.path.join(ROOT, "js8carte", "core.py"), encoding="utf-8") as f:
        return re.search(r'APP_VERSION = "([^"]+)"', f.read()).group(1)


def find_iscc():
    for path in (shutil.which("iscc"),
                 r"D:\ClaudeProjets\Outils\InnoSetup\ISCC.exe",
                 r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                 r"C:\Program Files\Inno Setup 6\ISCC.exe",
                 os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")):
        if path and os.path.exists(path):
            return path
    sys.exit("Inno Setup 6 introuvable (ISCC.exe) : https://jrsoftware.org/isdl.php")


def main():
    icon = make_icon()
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
           "--name", "JS8Carte", "--icon", icon, "--distpath", DIST, "--workpath", WORK, "--specpath", WORK,
           "--add-data", os.path.join(ROOT, "js8carte", "web") + os.pathsep + "js8carte/web",
           "--collect-all", "webview", os.path.join(ROOT, "JS8Carte.pyw")]
    subprocess.run(cmd, check=True, cwd=ROOT)
    print("\nTerminé :", os.path.join(DIST, "JS8Carte", "JS8Carte.exe"))
    if "--installer" in sys.argv:
        version = app_version()
        subprocess.run([find_iscc(), "/DAppVersion=" + version, os.path.join(HERE, "installer.iss")], check=True)
        print("Installeur :", os.path.join(DIST, "JS8Carte-Setup-%s.exe" % version))


if __name__ == "__main__":
    main()
