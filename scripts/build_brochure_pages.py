"""Render each brochure PDF to WebP page images for Lot Master's in-app brochure viewer.

Run by .github/workflows/lot-master-data.yml whenever a PDF under brochures/ is uploaded; also runnable by hand:
    python scripts/build_brochure_pages.py brochures brochure-pages

Each brochures/<name>.pdf becomes brochure-pages/<name>-1.webp, -2.webp, ... (long edge 2200px, quality 72) and an
entry in brochure-pages/manifest.json: {"<name>": {"v": <first 8 hex of the PDF's sha1>, "pages": [[w, h], ...]}}.
"v" busts the app's image cache when a PDF changes. PDFs whose hash already matches the manifest are skipped, and
pages of removed PDFs are deleted. Needs pdftoppm (poppler-utils) and Pillow.
"""
import glob, hashlib, json, os, subprocess, sys, tempfile

from PIL import Image

LONG_EDGE, QUALITY = 2200, 72


def render(pdf, out_dir, base):
    pages = []
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "200", "-png", pdf, os.path.join(td, "p")], check=True)
        for i, png in enumerate(sorted(glob.glob(os.path.join(td, "p*.png"))), 1):
            im = Image.open(png).convert("RGB")
            im.thumbnail((LONG_EDGE, LONG_EDGE), Image.LANCZOS)
            im.save(os.path.join(out_dir, f"{base}-{i}.webp"), "WEBP", quality=QUALITY, method=6)
            pages.append([im.size[0], im.size[1]])
    return pages


def main(src, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    man_path = os.path.join(out_dir, "manifest.json")
    manifest = json.load(open(man_path)) if os.path.exists(man_path) else {}
    done, skipped = [], 0
    pdfs = {os.path.splitext(os.path.basename(p))[0]: p for p in glob.glob(os.path.join(src, "*.pdf"))}
    for base, pdf in sorted(pdfs.items()):
        v = hashlib.sha1(open(pdf, "rb").read()).hexdigest()[:8]
        have = manifest.get(base)
        if have and have.get("v") == v and all(
                os.path.exists(os.path.join(out_dir, f"{base}-{i}.webp")) for i in range(1, len(have["pages"]) + 1)):
            skipped += 1
            continue
        for old in glob.glob(os.path.join(out_dir, f"{glob.escape(base)}-*.webp")):
            if old.rsplit("-", 1)[-1][:-5].isdigit():
                os.remove(old)
        manifest[base] = {"v": v, "pages": render(pdf, out_dir, base)}
        done.append(f"{base} ({len(manifest[base]['pages'])} pages)")
    removed = [b for b in list(manifest) if b not in pdfs]
    for b in removed:
        for i in range(1, len(manifest[b]["pages"]) + 1):
            p = os.path.join(out_dir, f"{b}-{i}.webp")
            if os.path.exists(p):
                os.remove(p)
        del manifest[b]
    if done or removed:
        with open(man_path, "w") as fh:
            json.dump(manifest, fh, separators=(",", ":"), sort_keys=True)
    lines = ["### Brochure page images", "", f"- {len(done)} rendered · {skipped} unchanged · {len(removed)} removed"]
    lines += [f"- rendered: {d}" for d in done] + [f"- removed: {b}" for b in removed]
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "brochures", sys.argv[2] if len(sys.argv) > 2 else "brochure-pages")
