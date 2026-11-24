"""Rebuild inventory.json from inv.xlsx, keeping everything the pricing sync adds.

Run by .github/workflows/lot-master-data.yml whenever inv.xlsx is uploaded; also runnable by hand:
    python scripts/build_inventory.py inv.xlsx inventory.json

Sheets read (header row first, matched by name):
  INV.MANAGER   Manufacturer | Brand | Model | Type | Year | Weight | Length | Status
  Key Features  Manufacturer | Brand | Model | KF1..KF6
  Brochures     Manufacturer | Brand | Model | ... | Brochure   (a PDF name under brochures/, no .pdf)

Rules
  - A unit is identified by Manufacturer + Brand + Model.
  - Existing units: specs, key features and brochure update from the sheet; every other field (stock, prices,
    availability, arrival dates, image path, tags...) is left exactly as it is, so the pricing sync's data survives.
  - New units are added next to their brand's other units, with the image path and id pattern of their siblings.
  - Units no longer in the sheet are KEPT (deleting one would wipe reps' study history) and listed in the summary.
"""
import json, os, re, sys
from datetime import datetime, timezone

import openpyxl

TYPE = {"TT": "Travel Trailer", "FW": "Fifth Wheel", "FWTH": "Fifth Wheel Toy Hauler",
        "TTTH": "Travel Trailer Toy Hauler", "C": "Class C Motorhome", "B": "Class B Motorhome",
        "A": "Class A Motorhome"}


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def length(v):
    s = clean(v)
    if not s:
        return None
    s = (s.replace("’", "'").replace("‘", "'").replace("”", '"').replace("“", '"')
          .replace("\\", ""))
    s = re.sub(r'^(\d+) (\d+)"$', lambda m: f"{m.group(1)}' {m.group(2)}\"", s)   # 30 11"  -> 30' 11"
    return s


def number(v):
    if isinstance(v, (int, float)):
        return int(v)
    s = clean(v)
    if s is None:
        return None
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return s                                               # keep "TBD" etc. as written


def rows(ws):
    data = [r for r in ws.iter_rows(values_only=True)]
    head = [clean(c) for c in data[0]]
    col = {h.lower(): i for i, h in enumerate(head) if h}
    out = []
    for r in data[1:]:
        get = lambda name, r=r: r[col[name]] if name in col and col[name] < len(r) else None   # bind this row
        if clean(get("manufacturer")) and clean(get("model")):
            out.append((r, get, col))
    return out


def key(get):
    return (clean(get("manufacturer")), clean(get("brand")), clean(get("model")))


def same_len(a, b):
    return (a or "").replace(" ", "") == (b or "").replace(" ", "")


def main(xlsx, inv_path):
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    inv = json.load(open(inv_path, encoding="utf-8"))
    units = inv["units"]
    by_key = {(u["manufacturer"], u["brand"], u["model"]): u for u in units}

    kf = {}
    if "Key Features" in wb.sheetnames:
        for r, get, col in rows(wb["Key Features"]):
            vals = [clean(get(f"kf{i}")) for i in range(1, 7)]
            kf[key(get)] = [v for v in vals if v]
    broch = {}
    if "Brochures" in wb.sheetnames:
        for r, get, col in rows(wb["Brochures"]):
            name = clean(get("brochure"))
            if name:
                broch[key(get)] = f"brochures/{name}.pdf"

    added, updated, kf_changed = [], [], []
    seen = set()
    for r, get, col in rows(wb["INV.MANAGER"]):
        k = key(get)
        seen.add(k)
        spec = {"year": number(get("year")), "weight": number(get("weight")), "length": length(get("length")),
                "status": clean(get("status"))}
        u = by_key.get(k)
        if u is None:
            sib = next((x for x in units if x["manufacturer"] == k[0] and x["brand"] == k[1]), None)
            uid = None
            if sib and sib["id"].endswith(sib["model"].lower()):
                uid = sib["id"][: -len(sib["model"])] + k[2].lower()
            uid = uid or re.sub(r"[^a-z0-9]+", "-", f"{k[0]}-{k[1]}-{k[2]}".lower()).strip("-")
            img = None
            if sib and sib.get("imagePath") and sib["model"].lower() in sib["imagePath"].rsplit("/", 1)[-1]:
                folder, file = sib["imagePath"].rsplit("/", 1)
                img = f"{folder}/{file.replace(sib['model'].lower(), k[2].lower())}"
            t = clean(get("type")) or ""
            u = {"id": uid, "manufacturer": k[0], "brand": k[1], "model": k[2], "type": TYPE.get(t, t),
                 "year": spec["year"], "price": None, "weight": spec["weight"], "length": spec["length"],
                 "imagePath": img, "status": spec["status"], "keyFeatures": kf.get(k) or None,
                 "brochure": broch.get(k)}
            last = max((i for i, x in enumerate(units) if x["manufacturer"] == k[0] and x["brand"] == k[1]),
                       default=len(units) - 1)
            units.insert(last + 1, u)
            by_key[k] = u
            added.append(k)
            continue
        changes = []
        for f in ("year", "weight", "status"):
            if spec[f] is not None and u.get(f) != spec[f]:
                changes.append(f"{f} {u.get(f)} -> {spec[f]}")
                u[f] = spec[f]
        if spec["length"] and not same_len(length(u.get("length")), spec["length"]):
            changes.append(f"length {u.get('length')} -> {spec['length']}")
            u["length"] = spec["length"]
        if k in broch and u.get("brochure") != broch[k]:
            changes.append(f"brochure -> {broch[k]}")
            u["brochure"] = broch[k]
        if changes:
            updated.append((k, changes))
        new_kf = kf.get(k)
        if new_kf and [s.strip() for s in (u.get("keyFeatures") or [])] != new_kf:
            u["keyFeatures"] = new_kf
            kf_changed.append(k)

    missing = [k for k in by_key if k not in seen]
    if added or updated or kf_changed:
        inv["unitCount"] = len(units)
        inv["version"] = f"inv-{datetime.now(timezone.utc):%Y.%m}-{len(units)}-brochures"
        with open(inv_path, "w", encoding="utf-8") as fh:
            json.dump(inv, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    name = lambda k: f"{k[0]} {k[1]} {k[2]}"
    lines = ["### inventory.json from inv.xlsx", "",
             f"- {len(units)} units · {len(added)} added · {len(updated)} spec updates · {len(kf_changed)} key-feature updates"]
    if added:
        lines += ["", "**Added**"] + [f"- {name(k)}" + ("" if by_key[k].get("imagePath") else " (no image path)") for k in added]
        no_img = [k for k in added if by_key[k].get("imagePath") and not os.path.exists(by_key[k]["imagePath"])]
        if no_img:
            lines += ["", "**Floorplan image still to upload**"] + [f"- `{by_key[k]['imagePath']}`" for k in no_img]
    if missing:
        lines += ["", "**In the app but not in the sheet (kept, not deleted)**"] + [f"- {name(k)}" for k in missing]
    if updated:
        lines += ["", "<details><summary>Spec changes</summary>", ""] + \
                 [f"- {name(k)}: " + "; ".join(c) for k, c in updated] + ["", "</details>"]
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "inv.xlsx", sys.argv[2] if len(sys.argv) > 2 else "inventory.json")
