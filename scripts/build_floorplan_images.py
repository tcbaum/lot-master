"""Make a small WebP copy of every floorplan image for Lot Master to load instead of the full-size PNG.

Run by .github/workflows/lot-master-data.yml whenever a floorplan image is uploaded; also runnable by hand:
    python scripts/build_floorplan_images.py . floorplans-web

Each <folder>/<name>.png (or .jpg) becomes floorplans-web/<folder>/<name>.webp, at most 2800px on the long edge,
quality 80, transparency kept. floorplans-web/manifest.json maps the ORIGINAL path to the first 8 hex of its sha1:
{"grand-design-influence/influence-3705rd.png": "1a2b3c4d", ...}. The app only uses a WebP that is listed there
(so a missing one falls back to the PNG), and the hash busts its image cache when a floorplan is replaced.
Originals are never touched. Unchanged images are skipped; copies of removed images are deleted.
Logos (logos/) stay PNG: they're tiny already. Needs Pillow.
"""
import glob, hashlib, json, os, sys
from concurrent.futures import ProcessPoolExecutor

from PIL import Image

LONG_EDGE, QUALITY = 2800, 80
SKIP_DIRS = {"logos", "brochures", "brochure-pages", "floorplans-web", "scripts", ".github", ".git"}
EXTS = (".png", ".jpg", ".jpeg")


def sources(root):
    out = []
    for path in glob.glob(os.path.join(root, "**", "*"), recursive=True):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel.split("/")[0] in SKIP_DIRS or "/" not in rel or not rel.lower().endswith(EXTS):
            continue                                   # top-level files (none expected) and skipped folders
        out.append(rel)
    return sorted(out)


def target(out_dir, rel):
    return os.path.join(out_dir, os.path.splitext(rel)[0] + ".webp")


def convert(args):
    src, dst = args
    im = Image.open(src)
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if has_alpha else "RGB")
    im.thumbnail((LONG_EDGE, LONG_EDGE), Image.LANCZOS)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    im.save(dst, "WEBP", quality=QUALITY, method=5)
    return os.path.getsize(src), os.path.getsize(dst)


def main(root, out_dir):
    man_path = os.path.join(out_dir, "manifest.json")
    manifest = json.load(open(man_path)) if os.path.exists(man_path) else {}
    rels = sources(root)
    todo, new_manifest = [], {}
    for rel in rels:
        v = hashlib.sha1(open(os.path.join(root, rel), "rb").read()).hexdigest()[:8]
        new_manifest[rel] = v
        if manifest.get(rel) != v or not os.path.exists(target(out_dir, rel)):
            todo.append(rel)
    removed = [rel for rel in manifest if rel not in new_manifest]
    for rel in removed:
        p = target(out_dir, rel)
        if os.path.exists(p):
            os.remove(p)

    before = after = 0
    failed = []
    if todo:
        with ProcessPoolExecutor() as ex:
            jobs = [(os.path.join(root, r), target(out_dir, r)) for r in todo]
            for rel, res in zip(todo, ex.map(convert_safe, jobs)):
                if res is None:
                    failed.append(rel)
                    new_manifest.pop(rel, None)        # not listed -> the app keeps using the PNG
                else:
                    before += res[0]
                    after += res[1]

    if todo or removed or set(new_manifest) != set(manifest):
        os.makedirs(out_dir, exist_ok=True)
        with open(man_path, "w") as fh:
            json.dump(new_manifest, fh, separators=(",", ":"), sort_keys=True)

    mb = lambda n: f"{n / 1048576:.1f} MB"
    lines = ["### Floorplan WebP copies", "",
             f"- {len(todo) - len(failed)} converted · {len(rels) - len(todo)} unchanged · {len(removed)} removed"]
    if before:
        lines.append(f"- converted images: {mb(before)} -> {mb(after)}")
    lines += [f"- could not convert (PNG still used): `{r}`" for r in failed]
    print("\n".join(lines))


def convert_safe(args):
    try:
        return convert(args)
    except Exception as e:                             # a broken upload shouldn't stop the rest
        print(f"warning: {args[0]}: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".", sys.argv[2] if len(sys.argv) > 2 else "floorplans-web")
