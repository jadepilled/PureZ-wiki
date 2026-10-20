"""Copy the named places of every map from the map repo into data/places.json for the wiki's location pages.

    python extract/places.py --maps ../PureZ-map/maps --out data/places.json
"""
import argparse, json, os

RANKS = ["Capital", "City", "Village", "Local", "Marine", "Camp", "Ruin"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", required=True, help="maps/ folder of the PureZ-map repo")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = {}
    with open(os.path.join(a.maps, "..", "maps.json"), encoding="utf-8") as f:
        order = [m["id"] for m in json.load(f)]
    for mid in order:
        path = os.path.join(a.maps, mid, "data.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        out[mid] = {"name": d.get("name", mid), "size": d.get("size"), "places": d.get("places") or []}
        print(mid, len(out[mid]["places"]), "places")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
