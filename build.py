"""Build the PureZ wiki into _site/.

    python build.py            # production build
    python build.py --preview  # also shows empty states for media and data not extracted yet

Inputs:
  content/   Markdown pages you edit. Front matter keys: class, category, title, summary, order.
  data/      JSON written by extract/*.py (mission.json, scripts.json, configs.json).
  media/     renders/<category>/<Class>.webp, models/<Class>.glb, sounds/<Class>/*.ogg
  templates/ Jinja2 page templates.  static/  CSS and JS.
"""
import argparse, json, math, os, re, shutil
from collections import defaultdict

import markdown
from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "_site")

CATEGORIES = [
    ("weapons", "Weapons", "Firearms and their handling, ballistics and recoil."),
    ("ammunition", "Ammunition", "Rounds, damage and ballistics."),
    ("attachments", "Attachments", "Optics, magazines, muzzles and furniture."),
    ("clothing", "Clothing", "Protection, storage and insulation."),
    ("gear", "Gear & tools", "Containers, tools and equipment."),
    ("medical", "Medical", "Treatment, drugs and what they cure."),
    ("food", "Food & drink", "Nutrition and safe consumption."),
    ("vehicles", "Vehicles", "Spawns, parts and capacities."),
    ("health", "Health", "Diseases and status effects."),
    ("crafting", "Crafting", "Every recipe and what it needs."),
    ("server", "Server", "Economy, infected and gameplay settings."),
]
CAT = {c[0]: c for c in CATEGORIES}

# Editorial names for script-defined conditions (scripts only have class names)
MAP_URL = "https://map.purez.gg/"

DISEASES = {  # modifier id: (name, agent, url slug)
    "mdf_cholera": ("Cholera", "cholera", "cholera"),
    "mdf_salmonella": ("Salmonella", "salmonella", "salmonella"),
    "mdf_influenza": ("Influenza", "influenza", "influenza"),
    "mdf_common_cold": ("Common cold", "influenza", "common-cold"),
    "mdf_pneumonia": ("Pneumonia", "influenza", "pneumonia"),
    "mdf_brain": ("Kuru", "brain", "kuru"),
    "mdf_heavymetal1": ("Heavy metal poisoning", "heavymetal", "heavy-metal-poisoning"),
    "mdf_poisoning": ("Food poisoning", "food_poison", "food-poisoning"),
    "mdf_wound_infection1": ("Wound infection", "wound_agent", "wound-infection"),
    "mdf_wound_infection2": ("Wound infection, severe", "wound_agent", "wound-infection-severe"),
    "mdf_contamination1": ("Chemical poisoning", "chemical_poison", "chemical-poisoning"),
    "mdf_contamination2": ("Chemical poisoning, moderate", "chemical_poison", "chemical-poisoning-moderate"),
    "mdf_contamination3": ("Chemical poisoning, severe", "chemical_poison", "chemical-poisoning-severe"),
}
DRUG_ITEMS_FALLBACK = {"antibiotics": ["TetracyclineAntibiotics"], "chelation": ["ChelatingTablets"]}

VEHICLE_NAMES = {"VehicleCivilianSedan": "Olga 24", "VehicleHatchback02": "Gunter 2", "VehicleSedan02": "Sarka 120",
                 "VehicleOffroadHatchback": "Ada 4x4", "VehicleOffroad02": "M1025", "VehicleTruck01": "M3S",
                 "VehicleMotorbike": "Motorbikes", "VehicleBoat": "Boats"}
GLOBAL_LABELS = {"TimeLogin": ("Login timer", "s"), "TimeLogout": ("Logout timer", "s"),
                 "TimeHopping": ("Server hop timer", "s"), "TimePenalty": ("Early logout penalty", "s"),
                 "CleanupLifetimeDeadPlayer": ("Body despawn", "dur"), "CleanupLifetimeRuined": ("Ruined item despawn", "dur")}
GAMEPLAY_LABELS = [
    (("PlayerData", "StaminaData", "sprintStaminaModifierErc"), "Sprint stamina cost, standing", "×"),
    (("PlayerData", "StaminaData", "sprintStaminaModifierCro"), "Sprint stamina cost, crouched", "×"),
    (("PlayerData", "StaminaData", "holdBreathStaminaModifier"), "Hold breath stamina cost", "×"),
    (("PlayerData", "StaminaData", "staminaWeightLimitThreshold"), "Stamina weight allowance", "g"),
    (("PlayerData", "MovementData", "timeToSprint"), "Time to reach sprint", "s"),
    (("PlayerData", "MovementData", "timeToStrafeJog"), "Strafe transition, jogging", "s"),
    (("PlayerData", "MovementData", "timeToStrafeSprint"), "Strafe transition, sprinting", "s"),
    (("PlayerData", "MovementData", "rotationSpeedJog"), "Turn smoothing, jogging", "s"),
    (("PlayerData", "MovementData", "rotationSpeedSprint"), "Turn smoothing, sprinting", "s"),
    (("PlayerData", "MovementData", "allowStaminaAffectInertia"), "Low stamina adds inertia", ""),
]

HEALTH_LEVELS = [("Pristine", 0.0, 0.3), ("Worn", 0.3, 0.5), ("Damaged", 0.5, 0.7), ("Badly damaged", 0.7, 1.0)]

REPAIR_KITS = {1: "WeaponCleaningKit", 2: "SewingKit", 3: "LeatherSewingKit", 4: "Whetstone", 5: "DuctTape",
               6: "TireRepairKit", 7: "ElectronicRepairKit", 8: "EpoxyPutty"}
WEAR_SLOTS = {"body": "Jacket", "legs": "Pants", "feet": "Shoes", "headgear": "Hat or helmet", "mask": "Face",
              "eyewear": "Eyes", "gloves": "Gloves", "back": "Backpack", "vest": "Vest", "hips": "Belt", "armband": "Armband",
              "shoulder": "Shoulder", "melee": "Melee", "pistol": "Holster", "bow": "Shoulder"}
ATTACH_GROUPS = [("optic", "Optics"), ("muzzle", "Muzzle"), ("suppressor", "Muzzle"), ("buttstock", "Buttstock"),
                 ("handguard", "Handguard"), ("flashlight", "Light"), ("bayonet", "Bayonet"), ("wrap", "Wrap"),
                 ("bipod", "Bipod"), ("barrel", "Barrel")]
ARMOR_KINDS = [("Projectile", "Bullets"), ("Melee", "Melee"), ("Infected", "Infected"), ("FragGrenade", "Explosions")]
BASE_FOV = 0.462    # field of view the game treats as 1x; magnification is relative to it
DEV_ITEM = re.compile(r"(?i)test|debug|dummy|_ground$")


def load_json(name):
    path = os.path.join(ROOT, "data", name)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def front_matter(text):
    meta = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        for line in text[3:end].strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[end + 4:]
    return meta, text.strip()


COLOUR = re.compile(r"(?i)(black|white|blue|green|red|yellow|orange|brown|gr[ae]y|beige|pink|violet|purple|olive|khaki|tan|"
                    r"camo|desert|winter|woodland|autumn|summer|spring|navy|navyblue|lightblue|darkblue|lime|wine|"
                    r"flecktarn|flecktran|dubok|dpm|bdu|un|ttsko|ion|\w*pattern|\w*checks?|stripes|"
                    r"darkbrown|darkgreen|lightgreen|cream|natural|gold|bluebright|skyblue|flowers|polka|"
                    r"blackskull|skull|pig|chain|scarred|yellowscarred)")
MEDICAL = re.compile(r"(?i)bandage|morphine|epinephrine|saline|bloodbag|bloodtest|tetracycline|charcoal|painkiller|"
                     r"vitamin|iodine|disinfect|splint|defibrillator|purification|chelating|startkit|thermometer|antichem")


def family(cls):
    """Colour variants share a page: Hoodie_Blue -> Hoodie."""
    base, _, suffix = cls.rpartition("_")
    return base if base and COLOUR.fullmatch(suffix) else cls


def wiki_category(cls, t):
    c = t.get("category")
    if c == "clothes":
        return "clothing"
    if c in ("food", "tools", "containers"):
        return "medical" if MEDICAL.search(cls) else ("food" if c == "food" else "gear")
    if c == "explosives":
        return "weapons"
    if c == "weapons":
        if re.match(r"(?i)ammo", cls):
            return "ammunition"
        if re.match(r"(?i)guncase", cls):
            return "gear"
        if re.match(r"(?i)mag_", cls) or re.search(r"(?i)optic|suppressor|bttstck|hndgrd|compensator|bayonet|light$|^nvg", cls):
            return "attachments"
        return "weapons"
    return "gear"


CALIBRES = {"45acp": ".45 ACP", "308win": ".308 Win", "357": ".357", "380": ".380", "22": ".22",
            "12gapellets": "12ga buckshot", "00buck": "12ga buckshot", "12gaslug": "12ga slug", "12garubberslug": "12ga rubber slug",
            "9x39ap": "9x39mm AP", "556x45": "5.56x45mm", "545x39": "5.45x39mm", "762x39": "7.62x39mm", "762x54": "7.62x54mmR",
            "9x19": "9x19mm", "9x39": "9x39mm", "huntingbolt": "Hunting bolt", "flare": "Flare", "flareblue": "Blue flare",
            "flaregreen": "Green flare", "flarered": "Red flare"}


def calibre(code):
    low = code.lower()
    tracer = low.endswith("tracer")
    low = low[:-6] if tracer else low
    name = CALIBRES.get(low)
    if not name:
        m = re.match(r"^(\d)(\d{2})x(\d+)$", low)
        name = f"{m.group(1)}.{m.group(2)}x{m.group(3)}mm" if m else humanize(code)
    return name + (" tracer" if tracer else "")


def sentence(t):
    """Sentence case that keeps acronyms and model codes: 'Blood Bag Empty' -> 'Blood bag empty'."""
    words = t.split(" ")
    return " ".join(w if i == 0 or not re.fullmatch(r"[A-Z][a-z]+", w) else w.lower() for i, w in enumerate(words))


# Stand-in spellings for class names that humanize() gets wrong, until config display names are extracted
TITLE_FIX = {"ASVAL": "AS VAL", "PSO11Optic": "PSO-1-1 optic", "PSO1Optic": "PSO-1 optic", "PSO6Optic": "PSO-6 optic",
             "Izh18": "IZH-18", "Izh18Shotgun": "IZH-18 shotgun", "Izh43Shotgun": "IZH-43 shotgun", "Mp133Shotgun": "MP-133 shotgun",
             "M18SmokeGrenade": "M18 smoke grenade", "M67Grenade": "M67 grenade", "RDG2SmokeGrenade": "RDG-2 smoke grenade",
             "RGD5Grenade": "RGD-5 grenade", "PM73Rak": "PM-73 RAK", "MakarovIJ70": "Makarov IJ-70", "LeeEnfield": "Lee-Enfield",
             "M68Optic": "M68 optic", "MK4Optic": "MK4 optic", "Mag_AKM_Palm30Rnd": "AKM 30rd magazine (palm)",
             "Fal_OeBttstck": "FAL OE buttstock", "Fal_FoldingBttstck": "FAL folding buttstock", "MP5k_StockBttstck": "MP5K stock buttstock",
             "Mag_Aug_30Rnd": "AUG 30rd magazine", "Aug": "AUG", "AugShort": "AUG short", "SVD_Wooden": "SVD, wooden",
             "Crossbow_Wood": "Crossbow, wooden", "Engraved1911": "Engraved 1911", "Colt1911": "Colt 1911"}


def item_title(cls):
    """Readable stand-in names until config display names are extracted."""
    if cls in TITLE_FIX:
        return TITLE_FIX[cls]
    m = re.match(r"(?i)^Ammo_40mm_(\w+)$", cls)
    if m:
        return sentence(f"40mm {humanize(m.group(1))} grenade")
    m = re.match(r"(?i)^AmmoBox_(.+?)_(\d+)rnd$", cls)
    if m:
        return f"{calibre(m.group(1))} ammo box, {m.group(2)} rounds"
    m = re.match(r"(?i)^Ammo_(\w+)$", cls)
    if m:
        c = calibre(m.group(1))
        return c if re.search(r"(?i)bolt|flare", c) else f"{c} rounds"
    m = re.match(r"(?i)^Mag_(.+?)_(?:Drum)?(\d+)Rnd(?:_(\w+))?$", cls)
    if m:
        return f"{humanize(m.group(1))} {m.group(2)}rd {'drum' if 'drum' in cls.lower() else 'magazine'}" + (f" ({humanize(m.group(3)).lower()})" if m.group(3) else "")
    t = humanize(cls)
    for a, b in (("Bttstck", "Buttstock"), ("Hndgrd", "Handguard")):
        t = t.replace(a, b)
    t = re.sub(r"(\d)(Optic|Shotgun|Grenade)\b", r"\1 \2", t)
    return sentence(t)


def variant_label(cls, base):
    if cls == base:
        return "Standard"
    suffix = cls[len(base):].lstrip("_") if cls.startswith(base) else cls.rsplit("_", 1)[-1] if "_" in cls else cls
    return humanize(suffix) if suffix else "Standard"


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower().replace("×", "x")).strip("-")


def humanize(cls):
    s = re.sub(r"_(?=[A-Z0-9])", " ", cls)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    s = re.sub(r"(?<=[a-z]{2})(?=\d)", " ", s)
    return s.replace("_", " ")


class Wiki:
    def __init__(self, preview, base=""):
        self.preview = preview
        self.base = base.rstrip("/")          # URL prefix when served from a subpath (github.io/<repo>)
        self.mission = load_json("mission.json")
        self.scripts = load_json("scripts.json")
        self.configs = load_json("configs.json")
        self.purez = self.mission.get("purez", {})
        self.pages = []          # every routable page: dict(url, title, category, ...)
        self.by_class = {}       # class -> item page
        self.md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])

    # ------------------------------------------------------------------ lookups
    def cfg(self, cls):
        for sec in ("weapons", "magazines", "vehicles"):
            c = self.configs.get(sec, {}).get(cls)
            if c:
                return sec, c
        return None, None

    def title(self, cls, meta=None):
        if meta and meta.get("title"):
            return meta["title"]
        _, c = self.cfg(cls)
        if c and c.get("displayName") and not str(c["displayName"]).startswith("$"):
            return c["displayName"]
        return humanize(cls)

    def link(self, cls):
        p = self.by_class.get(cls)
        return {"cls": cls, "title": p["title"] if p else self.title(cls), "url": p["url"] if p else None}

    def links(self, classes):
        """Links without duplicates: colour variants share a page, dev items are dropped."""
        out, seen = [], set()
        for c in classes:
            if DEV_ITEM.search(c) or DEV_ITEM.search(self.title(c)):
                continue
            l = self.link(c)
            key = l["url"] or l["title"]
            if key in seen:
                continue
            seen.add(key)
            out.append(l)
        return out

    # ------------------------------------------------------------------ data blocks
    def loot(self, cls):
        t = self.purez.get("types", {}).get(cls)
        if not t:
            return None
        st = self.purez.get("spawnable", {}).get(cls) or {}
        g = self.purez.get("globals", {})
        dmg = st.get("damage") or [g.get("LootDamageMin", 0), g.get("LootDamageMax", 0.82)]
        carried = defaultdict(list)
        for zmb, chance in (self.mission.get("carriedBy", {}).get(cls) or {}).items():
            carried[zombie_group(zmb)].append(chance)
        return {
            "nominal": t["nominal"], "min": t["min"], "lifetime": t["lifetime"], "restock": t["restock"],
            "usage": t["usage"], "tiers": [x.replace("Tier", "") for x in t["value"] if x.startswith("Tier")],
            "unique": "Unique" in t["value"],
            "quant": [t["quantmin"], t["quantmax"]] if t["quantmin"] not in (None, -1) else None,
            "condition": condition_bar(dmg),
            "attachments": self.spawn_groups(st.get("groups", [])),
            "carried": sorted(((k, max(v)) for k, v in carried.items()), key=lambda x: -x[1]),
            "spawns": t["nominal"] > 0,
        }

    def spawn_groups(self, groups):
        out = []
        for g in groups:
            if g.get("preset") or g.get("kind") != "attachments":
                continue
            total = sum(c for _, c in g["items"]) or 1
            out.append({"chance": g["chance"], "items": [
                {**self.link(i), "share": c / total} for i, c in sorted(g["items"], key=lambda x: -x[1])]})
        return out

    def weapon(self, cls):
        _, c = self.cfg(cls)
        rec_cls = self.scripts.get("weaponRecoil", {}).get(cls)
        rec = self.scripts.get("recoil", {}).get(rec_cls) if rec_cls else None
        out = {}
        if rec:
            out["recoil"] = recoil_view(rec, self.scripts.get("recoil", {}))
            out["recoilClass"] = rec_cls
        if c:
            out["modes"] = [{"name": k, **v} for k, v in (c.get("_modes") or {}).items()]
            out["magazines"] = [self.link(m) for m in (c.get("magazines") or [])]
            sounds = []
            for m in out["modes"]:
                for set_name, samples in (m.get("sounds") or {}).items():
                    for s in samples[:1]:
                        sounds.append({"label": m["name"], "file": media_sound(cls, s["path"])})
            out["sounds"] = [s for s in sounds if s["file"]] or None
        return out or None

    def ballistics(self, cls):
        _, c = self.cfg(cls)
        if not c or not c.get("_ballistics"):
            return None
        ammo = self.configs.get("ammo", {}).get(c.get("ammo"), {})
        dmg = ammo.get("DamageApplied", {})
        return {"table": c["_ballistics"], "damage": {k: (dmg.get(k) or {}).get("damage") for k in ("Health", "Blood", "Shock")},
                "initSpeed": c.get("initSpeed"), "count": c.get("count"), "typicalSpeed": ammo.get("typicalSpeed")}

    def medical(self, cls):
        mods = self.scripts.get("itemModifiers", {}).get(cls)
        if not mods:
            return None
        out = []
        for m in mods:
            mod = self.scripts.get("modifiers", {}).get(m, {})
            const = mod.get("constants", {})
            life = next((v for k, v in const.items() if k.endswith("LIFETIME")), None)
            strength = next((v for k, v in const.items() if k.endswith("STRENGTH")), None)
            cures = []
            for drug in mod.get("drugs", []):
                for agent, a in self.scripts.get("agents", {}).items():
                    eff = drug_effect(strength, life, a, drug)
                    if eff and eff["net"] > 0:
                        cures.append({"agent": agent, **eff, "page": self.disease_for_agent(agent)})
            for agent_id in (mod.get("kills") or {}):
                eff = kill_effect(mod, agent_id)
                if eff:
                    cures.append({"agent": agent_id, **eff, "page": self.disease_for_agent(agent_id)})
            cures.sort(key=lambda c: -c["perDose"])
            out.append({"id": m, "name": humanize(m.replace("mdf_", "").title()), "duration": life,
                        "strength": strength, "drugs": mod.get("drugs", []), "cures": cures})
        return out

    def disease_for_agent(self, agent):
        for mid, (name, ag, slug) in DISEASES.items():
            if ag == agent:
                return {"title": name, "url": f"/health/{slug}/"}
        return None

    def recipes_for(self, cls):
        used, made = [], []
        for r in self.scripts.get("recipes", []):
            r = r | {"title": recipe_title(r), "results": list(dict.fromkeys(r["results"]))}
            if any(cls in ing for ing in r["ingredients"]):
                used.append(r)
            if cls in r["results"]:
                made.append(r)
        key = lambda r: r["title"]
        return {"used": sorted(used, key=key), "made": sorted(made, key=key)} if (used or made) else None

    def vehicle(self, cls):
        ev = next(((n, e) for n, e in self.purez.get("events", {}).items() if cls in e["children"]), None)
        if not ev:
            return None
        name, e = ev
        st = self.purez.get("spawnable", {}).get(cls) or {}
        counts = {}
        for g in st.get("groups", []):
            for i, c in g.get("items", []):
                slot = counts.setdefault(i, {"n": 0, "chance": 1.0})
                slot["n"] += 1
                slot["chance"] = min(slot["chance"], g["chance"] * c)
        parts = [self.link(i) | part for i, part in counts.items()]
        return {"event": name, "nominal": e["nominal"], "positions": e["positions"],
                "variants": [self.link(x) | {"title": variant_label(x, family(cls) if x.startswith(family(cls)) else x.rsplit("_", 1)[0])} for x in e["children"] if x != cls], "parts": parts,
                "condition": condition_bar(st.get("damage") or [0, 0])}

    # ------------------------------------------------------------------ pages
    def collect(self):
        for dirpath, _, files in os.walk(os.path.join(ROOT, "content")):
            for f in sorted(files):
                if not f.endswith(".md"):
                    continue
                path = os.path.join(dirpath, f)
                rel = os.path.relpath(path, os.path.join(ROOT, "content")).replace(os.sep, "/")
                with open(path, encoding="utf-8") as fh:
                    meta, body = front_matter(fh.read())
                cat = meta.get("category") or rel.split("/")[0]
                slug = rel[:-3]
                if slug == "index":
                    url = "/"
                elif slug.endswith("/index"):
                    url = "/" + slug[:-6] + "/"
                else:
                    url = "/" + slug.lower().replace("_", "-") + "/"
                default_title = CAT[cat][1] if slug.endswith("index") and cat in CAT else humanize(slug.split("/")[-1])
                page = {"url": url, "meta": meta, "body": body, "category": cat, "src": rel,
                        "cls": meta.get("class"), "title": self.title(meta["class"], meta) if meta.get("class") else meta.get("title", default_title)}
                if page["cls"]:
                    page["url"] = f"/{cat}/{meta.get('slug') or slugify(page['title'])}/"
                if meta.get("disease") in DISEASES:
                    page["disease"] = meta["disease"]
                    page["title"] = DISEASES[meta["disease"]][0]
                    page["url"] = f"/health/{DISEASES[meta['disease']][2]}/"
                self.pages.append(page)
                if page["cls"]:
                    self.by_class[page["cls"]] = page
        self.auto_pages()
        self.index_configs()
        # script-defined conditions get pages automatically
        for mid, (name, agent, slug) in DISEASES.items():
            if mid in self.scripts.get("modifiers", {}):
                url = f"/health/{slug}/"
                if not any(p["url"] == url for p in self.pages):
                    self.pages.append({"url": url, "meta": {}, "body": "", "src": "", "category": "health", "disease": mid, "title": name})

    def index_configs(self):
        """Slot and ammo lookups across every config class that has a wiki page."""
        self.by_slot = defaultdict(list)      # inventory slot (lower) -> classes that fit it
        self.by_ammo = defaultdict(list)      # bullet class -> magazines and piles using it
        self.weapons_by_mag = defaultdict(list)
        self.weapons_by_slot = defaultdict(list)
        self.weapons_by_chamber = defaultdict(list)
        seen = set()
        for cls, page in self.by_class.items():
            sec, c = self.cfg(cls)
            if not c or page["cls"] in seen and cls != page["cls"]:
                continue
            seen.add(page["cls"])
            for slot in c.get("inventorySlot") or []:
                self.by_slot[slot.lower()].append(page["cls"])
            if sec == "magazines" and c.get("ammo"):
                self.by_ammo[c["ammo"]].append(page["cls"])
            if sec == "weapons":
                for m in c.get("magazines") or []:
                    self.weapons_by_mag[m].append(cls)
                for slot in c.get("attachments") or []:
                    self.weapons_by_slot[slot.lower()].append(cls)
                for a in c.get("chamberableFrom") or []:
                    self.weapons_by_chamber[a].append(cls)
        for d in (self.by_slot, self.by_ammo, self.weapons_by_mag, self.weapons_by_slot, self.weapons_by_chamber):
            for k in d:
                d[k] = sorted(dict.fromkeys(d[k]), key=lambda x: self.title(x))

    def fits(self, cls):
        """Where an attachment goes: the weapons or clothing with a matching slot."""
        _, c = self.cfg(cls)
        out = []
        for slot in (c or {}).get("inventorySlot") or []:
            out += self.weapons_by_slot.get(slot.lower(), [])
            out += [h for h, pg in self.by_class.items() if h == pg["cls"] and h != cls and
                    any(a.lower() == slot.lower() for a in (self.cfg(h)[1] or {}).get("attachments") or [])]
        out += self.weapons_by_mag.get(cls, [])
        return self.links(sorted(dict.fromkeys(out), key=lambda x: self.title(x)))

    def facts(self, cls, cat):
        """Category-specific details from the config, in the units players use."""
        sec, c = self.cfg(cls)
        if not c:
            return {}
        out = {}
        kits = [REPAIR_KITS[k] for k in (c.get("repairableWithKits") or []) if k in REPAIR_KITS]
        if kits:
            out["repair"] = self.links(kits)
        slots = [WEAR_SLOTS[x.lower()] for x in (c.get("inventorySlot") or []) if x.lower() in WEAR_SLOTS]
        if cat == "clothing" and slots:
            out["worn"] = ", ".join(dict.fromkeys(slots))
        armor = dig(c, ("DamageSystem", "GlobalArmor")) or {}
        rows = []
        for key, label in ARMOR_KINDS:
            a = armor.get(key)
            if a:
                rows.append({"label": label, "health": 1 - (dig(a, ("Health", "damage")) if dig(a, ("Health", "damage")) is not None else 1),
                             "shock": 1 - (dig(a, ("Shock", "damage")) if dig(a, ("Shock", "damage")) is not None else 1)})
        if any(r["health"] > 0.001 or r["shock"] > 0.001 for r in rows):
            out["armor"] = rows
        holds = [x for x in (c.get("attachments") or []) if self.by_slot.get(x.lower())]
        if holds and cat != "vehicles" and sec != "weapons":
            items = sorted(dict.fromkeys(i for x in holds for i in self.by_slot[x.lower()] if i != cls), key=lambda x: self.title(x))
            if items:
                out["holds"] = self.links(items[:24])
        q, unit = c.get("varQuantityMax"), (c.get("stackedUnit") or "").strip()
        if q and sec != "magazines":
            if unit in ("ml", "g"):
                out["capacity"] = f"{q:g} {unit}"
            elif unit in ("pills", "pc.", "pc", "w", "%", ""):
                label = {"pills": "pills", "pc.": "uses", "pc": "uses", "w": "% charge", "%": "%", "": ""}[unit]
                out["capacity"] = f"{q:g} {label}".strip() if unit not in ("w", "%") else f"{q:g}{label}"
        nut = c.get("Nutrition")
        if nut and cat == "food":
            out["nutrition"] = [(k, v) for k, v in (("Energy", nut.get("energy") and f"{nut['energy']:g} kcal"),
                                                    ("Water", nut.get("water") and f"{nut['water']:g} ml"),
                                                    ("Fills the stomach", nut.get("fullnessIndex") and f"×{nut['fullnessIndex']:g}"),
                                                    ("Nutrition", nut.get("nutritionalIndex") and f"×{nut['nutritionalIndex']:g}"),
                                                    ("Toxicity", nut.get("toxicity") and f"{nut['toxicity']:g}")) if v]
        mm = c.get("MeleeModes")
        if mm and cat in ("gear", "weapons") and c.get("isMeleeWeapon"):
            modes = []
            for name in ("Default", "Heavy"):
                m = mm.get(name) or {}
                a = self.configs.get("ammo", {}).get(m.get("ammo") or "")
                if a and not str(m.get("ammo", "")).startswith("MeleeFist"):
                    dmg = a.get("DamageApplied", {})
                    modes.append({"name": "Light" if name == "Default" else "Heavy", "health": dig(dmg, ("Health", "damage")),
                                  "shock": dig(dmg, ("Shock", "damage")), "range": m.get("range")})
            if modes:
                out["melee"] = modes
        if sec == "weapons":
            out["chambers"] = self.links(c.get("chamberableFrom") or [])
            groups = defaultdict(list)
            for slot in c.get("attachments") or []:
                label = next((g for key, g in ATTACH_GROUPS if key in slot.lower()), None)
                if label:
                    groups[label] += self.by_slot.get(slot.lower(), [])
            out["attachments"] = [{"group": g, "items": self.links(sorted(dict.fromkeys(v), key=lambda x: self.title(x)))} for g, v in groups.items() if v]
            if c.get("discreteDistance"):
                out["zeroing"] = c["discreteDistance"]
            mags = c.get("magazines") or []
            first = next((self.configs["magazines"].get(m) for m in mags if self.configs["magazines"].get(m)), None)
            if not first:
                first = next((self.configs["magazines"].get(m) for m in (c.get("chamberableFrom") or []) if self.configs["magazines"].get(m)), None)
            if first:
                a = self.configs.get("ammo", {}).get(first.get("ammo") or "", {})
                out["damage"] = {"health": dig(a, ("DamageApplied", "Health", "damage")), "shock": dig(a, ("DamageApplied", "Shock", "damage")),
                                 "velocity": first.get("initSpeed")}
        if sec == "magazines":
            out["fits"] = self.links(self.weapons_by_mag.get(cls, []) or self.weapons_by_chamber.get(cls, []))
            if c.get("ammo"):
                related = [m for m in self.by_ammo.get(c["ammo"], []) if m != cls]
                out["sameRound"] = self.links(related)
            a = self.configs.get("ammo", {}).get(c.get("ammo") or "", {})
            if a.get("weight") and (c.get("count") or 1) > 1 and abs(a["weight"] * 1000 - (c.get("weight") or 0)) > 0.5:
                out["roundWeight"] = f"{a['weight'] * 1000:g} g"
        if sec == "vehicles" and cat == "attachments":
            out["fits"] = self.fits(cls)
            oi = c.get("OpticsInfo") or {}
            if oi.get("opticsZoomMin"):
                stated = re.findall(r"(\d+(?:\.\d+)?)x\b", str(c.get("descriptionShort") or ""))
                lo, hi = BASE_FOV / oi["opticsZoomMin"], BASE_FOV / (oi.get("opticsZoomMax") or oi["opticsZoomMin"])
                if stated:
                    out["zoom"] = "–".join(dict.fromkeys(stated)) + "×"
                elif lo > 1.2:
                    out["zoom"] = f"{lo:.1f}×" if abs(hi - lo) < 0.05 else f"{min(lo, hi):.1f}–{max(lo, hi):.1f}×"
                if oi.get("discreteDistance"):
                    d = oi["discreteDistance"]
                    out["zeroing"] = d
        if cat == "vehicles" and not self.vehicle(cls):
            parts = defaultdict(int)
            for slot in c.get("attachments") or []:
                for item in self.by_slot.get(slot.lower(), [])[:1]:
                    parts[item] += 1
            out["parts"] = [self.link(i) | {"n": n} for i, n in parts.items()]
            if c.get("fuelCapacity"):
                out["fuel"] = f"{c['fuelCapacity']:g} l"
        return out

    def auto_pages(self):
        """A page for every item family that spawns on PureZ, and for every vehicle type."""
        types = self.purez.get("types", {})
        by_base = {}
        for cls, t in types.items():
            if (t["nominal"] or 0) <= 0 or t["category"] in (None, "lootdispatch"):
                continue
            by_base.setdefault(family(cls), []).append(cls)
        fams = {}
        for base, members in by_base.items():  # a family takes its members' most common category
            cats = [wiki_category(m, types[m]) for m in members]
            cat = max(set(cats), key=cats.count)
            # items the game names identically (Teddy Bear, Brass Knuckles) share a page as variants
            name = next((self.cfg(m)[1].get("displayName") for m in members if self.cfg(m)[1] and self.cfg(m)[1].get("displayName")), None)
            key = (f"name:{name}", cat) if name and not str(name).startswith("$") else (base, cat)
            fams.setdefault(key, []).extend(m for m in members if m not in fams.get(key, []))
        fams = {(k[0].split("name:", 1)[-1] if k[0].startswith("name:") else k[0], k[1]): v for k, v in fams.items()}
        for name, e in self.purez.get("events", {}).items():
            if name.startswith("Vehicle") and e["active"] and e["nominal"] > 0 and e["children"]:
                fams[(name, "vehicles")] = list(e["children"])
        taken = {p["url"] for p in self.pages}
        for (base, cat), members in sorted(fams.items()):
            members.sort(key=lambda c: (-(types.get(c, {}).get("nominal") or 0), c))
            existing = next((self.by_class[m] for m in members if m in self.by_class), None)
            if existing:
                existing["variants"] = members if len(members) > 1 else None
                for m in members:
                    self.by_class.setdefault(m, existing)
                continue
            rep_cls = base if base in members else members[0]
            if DEV_ITEM.search(base) and all(DEV_ITEM.search(m) for m in members):
                continue
            named = next((m for m in [rep_cls] + members if self.cfg(m)[1] and self.cfg(m)[1].get("displayName")), None)
            title = VEHICLE_NAMES.get(base) if cat == "vehicles" else self.title(named, {"title": None}) if named else item_title(base)
            slug = slugify(title)
            url = f"/{cat}/{slug}/"
            if url in taken:
                url = f"/{cat}/{slug}-{re.sub(r'[^a-z0-9]+', '-', base.lower())}/"
            taken.add(url)
            page = {"url": url, "meta": {}, "body": "", "category": cat, "src": "", "cls": rep_cls, "title": title,
                    "variants": members if len(members) > 1 else None, "auto": True}
            self.pages.append(page)
            for m in members:
                self.by_class[m] = page

    def render(self, env):
        if os.path.exists(OUT):
            shutil.rmtree(OUT)
        shutil.copytree(os.path.join(ROOT, "static"), os.path.join(OUT, "static"))
        if os.path.isdir(os.path.join(ROOT, "media")):
            shutil.copytree(os.path.join(ROOT, "media"), os.path.join(OUT, "media"))
        for extra in ("CNAME",):
            if os.path.exists(os.path.join(ROOT, extra)):
                shutil.copy(os.path.join(ROOT, extra), OUT)

        index = []
        for p in self.pages:
            ctx = self.context(p)
            tpl = ("home.html" if p["url"] == "/" else
                   "item.html" if p.get("cls") else
                   "disease.html" if p.get("disease") else
                   "server.html" if p["url"] == "/server/" else
                   "crafting.html" if p["url"] == "/crafting/" else
                   "category.html" if p["src"].endswith("index.md") and p["category"] in CAT else "page.html")
            html = env.get_template(tpl).render(**ctx)
            if self.base:
                html = re.sub(r'(href|src)="/', rf'\1="{self.base}/', html)
            dest = os.path.join(OUT, p["url"].strip("/"), "index.html")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(html)
            if p["url"] != "/":
                index.append({"t": p["title"], "c": p.get("cls") or "", "u": p["url"],
                              "k": CAT[p["category"]][1] if p["category"] in CAT else ""})
        with open(os.path.join(OUT, "static", "search.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, separators=(",", ":"))
        # class -> page title, colour variants included; the map uses this for item names
        with open(os.path.join(OUT, "static", "names.json"), "w", encoding="utf-8") as f:
            json.dump({c: p["title"] for c, p in sorted(self.by_class.items())}, f, separators=(",", ":"))
        with open(os.path.join(OUT, "404.html"), "w", encoding="utf-8") as f:
            html = env.get_template("page.html").render(**self.base_ctx(), page={"title": "Not found", "category": ""},
                                                        body="<p>That page doesn't exist. Try the search.</p>")
            f.write(re.sub(r'(href|src)="/', rf'\1="{self.base}/', html) if self.base else html)
        return len(self.pages)

    def base_ctx(self):
        return {"categories": CATEGORIES, "preview": self.preview}

    def context(self, p):
        self.md.reset()
        ctx = self.base_ctx() | {"page": p, "body": self.md.convert(p["body"]) if p["body"] else "",
                                 "cat": CAT.get(p["category"])}
        cls = p.get("cls")
        if cls:
            sec, c = self.cfg(cls)
            loot = None if self.vehicle(cls) else self.loot(cls)
            variants = None
            if p.get("variants"):
                types = self.purez.get("types", {})
                base = family(cls)
                variants = [{"cls": v, "label": variant_label(v, base), "nominal": (types.get(v) or {}).get("nominal")} for v in p["variants"]]
                if loot:
                    loot["nominal"] = sum((types.get(v) or {}).get("nominal") or 0 for v in p["variants"])
                    loot["min"] = sum((types.get(v) or {}).get("min") or 0 for v in p["variants"])
            map_url = f"{MAP_URL}#nasdara?i={cls}&l=buildings" if loot and loot["usage"] and loot["spawns"] else None
            ctx["item"] = {
                "cls": cls, "config": c, "description": p["meta"].get("summary") or (c or {}).get("descriptionShort"),
                "loot": loot, "variants": variants, "mapUrl": map_url, "weapon": self.weapon(cls), "ballistics": self.ballistics(cls),
                "medical": self.medical(cls), "recipes": self.recipes_for(cls), "vehicle": self.vehicle(cls),
                "media": media(p["category"], cls), "specs": specs(c, p["category"]), "facts": self.facts(cls, p["category"]),
            }
        if p.get("disease"):
            ctx["disease"] = self.disease(p["disease"])
        if p["url"] == "/server/":
            ctx["server"] = self.server()
        if p["url"] == "/crafting/":
            ctx["recipes"] = [r | {"title": recipe_title(r)} for r in self.scripts.get("recipes", [])]
            ctx["link"] = self.link
        if p["url"] == "/":
            ctx["counts"] = {k: sum(1 for x in self.pages if x["category"] == k and x["url"] != f"/{k}/") for k, *_ in CATEGORIES}
        if p["src"].endswith("index.md") and p["category"] in CAT and p["url"] != "/":
            ctx["children"] = sorted((x for x in self.pages if x["category"] == p["category"] and x is not p),
                                     key=lambda x: (int(x["meta"].get("order", 999)), x["title"]))
        return ctx

    def disease(self, mid):
        name, agent_id, _ = DISEASES[mid]
        mod = self.scripts["modifiers"][mid]
        agent = self.scripts.get("agents", {}).get(agent_id)
        const = mod.get("constants", {})
        treatments = []
        if agent:
            for item, mods in self.scripts.get("itemModifiers", {}).items():
                for m in mods:
                    dm = self.scripts["modifiers"].get(m, {})
                    dconst = dm.get("constants", {})
                    strength = next((v for k, v in dconst.items() if k.endswith("STRENGTH")), None)
                    life = next((v for k, v in dconst.items() if k.endswith("LIFETIME")), None)
                    kill = kill_effect(dm, agent_id)
                    if kill:
                        treatments.append({"drug": "direct", "item": self.link(item), **kill,
                                           "doses": math.ceil(const_or(mod.get("constants", {}).get("AGENT_THRESHOLD_ACTIVATE"), agent.get("MaxCount")) / kill["perDose"])})
                    for drug in dm.get("drugs", []):
                        eff = drug_effect(strength, life, agent, drug)
                        if eff and eff["net"] > 0:
                            treatments.append({"drug": drug, "item": self.link(item), **eff,
                                               "doses": math.ceil(const_or(mod.get("constants", {}).get("AGENT_THRESHOLD_ACTIVATE"), agent.get("MaxCount")) / eff["perDose"]) if eff["perDose"] else None})
            treatments.sort(key=lambda t: -t["perDose"])
        return {"id": mid, "name": name, "agentId": agent_id, "agent": agent, "modifier": mod,
                "activate": const.get("AGENT_THRESHOLD_ACTIVATE"), "deactivate": const.get("AGENT_THRESHOLD_DEACTIVATE"),
                "constants": [const_row(k, v) for k, v in const.items()
                              if not k.startswith("AGENT_THRESHOLD") and not re.search(CONST_HIDDEN, k)],
                "treatments": treatments, "symptoms": [s.replace("_", " ") for s in mod.get("symptoms", [])]}

    def server(self):
        g = self.purez.get("globals", {})
        globals_rows = [{"label": label, "unit": unit, "value": g.get(k)} for k, (label, unit) in GLOBAL_LABELS.items() if k in g]
        cats = defaultdict(int)
        for t in self.purez.get("types", {}).values():
            if t["category"] and t["category"] != "lootdispatch":
                cats[t["category"]] += t["nominal"] or 0
        loot_rows = sorted(({"category": k, "count": v} for k, v in cats.items() if v), key=lambda r: -r["count"])
        zones = sorted(({"name": humanize(z.replace("Infected", "")).replace("NBCYellow", "NBC") or z, "zones": d["zones"],
                         "range": [d["min"], d["max"]]} for z, d in self.purez.get("zombies", {}).items() if d["max"]),
                       key=lambda r: -r["range"][1])
        veh = sorted(({"name": VEHICLE_NAMES.get(n, humanize(n.replace("Vehicle", ""))), "count": e["nominal"],
                       "positions": e["positions"]} for n, e in self.purez.get("events", {}).items()
                      if n.startswith("Vehicle") and e["active"]), key=lambda r: -r["count"])
        gp = self.purez.get("gameplay") or {}
        gameplay_rows = [{"label": label, "unit": unit, "value": dig(gp, path)} for path, label, unit in GAMEPLAY_LABELS
                         if dig(gp, path) is not None]
        return {"globals": globals_rows, "loot": loot_rows, "lootTotal": sum(r["count"] for r in loot_rows),
                "zones": zones, "vehicles": veh, "gameplay": gameplay_rows}


# ---------------------------------------------------------------------- helpers
def const_or(v, fallback):
    return v if v is not None else fallback


def drug_effect(strength, lifetime, agent, drug):
    """Agents a dose removes. Drugs attack every agent present at strength x (1 - resistance);
    agents resist both drugs fully unless their script lowers it. Natural growth or die-off
    (which depends on immunity) runs alongside and is shown on the disease page."""
    if not strength or not lifetime:
        return None
    res = agent.get("drugResistance", {}).get(drug, 1.0)
    rate = strength * (1 - res)
    return {"resistance": res, "rate": rate, "net": rate, "perDose": round(rate * lifetime),
            "varies": drug in agent.get("specialDrugHandling", [])}


def kill_effect(mod, agent_id):
    """Modifiers that remove one agent directly (charcoal -> salmonella)."""
    rate = (mod.get("kills") or {}).get(agent_id)
    if not rate:
        return None
    start, end = mod.get("killWindow") or [0, next((v for k, v in mod.get("constants", {}).items() if k.endswith("LIFETIME")), 0)]
    return {"resistance": 0, "rate": rate, "net": rate, "perDose": round(rate * (end - start)), "varies": False,
            "delay": start}


def dig(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def zombie_group(cls):
    c = cls.lower()
    for key, label in [("soldier", "Military"), ("patrol", "Military"), ("ion", "Military"), ("police", "Police"),
                       ("firefighter", "Firefighters"), ("paramedic", "Medics"), ("doctor", "Medics"), ("nurse", "Medics"),
                       ("patient", "Medics"), ("hunter", "Hunters"), ("hiker", "Hikers"), ("hermit", "Hermits"),
                       ("construct", "Workers"), ("mechanic", "Workers"), ("handyman", "Workers"), ("worker", "Workers"),
                       ("bluecollar", "Workers"), ("skater", "Skaters"), ("jogger", "Joggers"), ("prisoner", "Prisoners"),
                       ("pilot", "Pilots"), ("farmer", "Farmers"), ("milkmaid", "Farmers"), ("villager", "Villagers"),
                       ("fisherman", "Fishermen"), ("priest", "Clergy"), ("imam", "Clergy"), ("nbc", "NBC")]:
        if key in c:
            return label
    return "Civilians"


def condition_bar(dmg):
    lo, hi = dmg
    segs = []
    for name, a, b in HEALTH_LEVELS:
        overlap = max(0.0, min(hi, b) - max(lo, a))
        segs.append({"name": name, "on": overlap > 0 or (lo == hi and a <= lo < b)})
    return {"range": [lo, hi], "levels": segs,
            "label": label_range([s["name"] for s in segs if s["on"]])}


CONST_UNITS = [  # (name contains, unit) checked in order
    ("CHANCE", "%"), ("PROBABILITY", "%"), ("TEMPERATURE", "°C"),
    ("WATER_LOSS", "ml/s"), ("WATER", "ml"), ("ENERGY", "kcal"), ("VOLUME", "ml"), ("BLOOD", "ml"),
    ("DAMAGE_PER_SEC", "HP/s"), ("HEALTH_LOSS", "HP/s"), ("PER_HOUR", "/h"),
    ("LIFETIME", "dur"), ("DURATION", "dur"), ("_TIME", "dur"), ("INTERVAL", "dur"), ("HEALTH", "HP"),
    ("THRESHOLD", "agents"),
]


CONST_LABELS = [  # (regex, label, unit) for constants whose raw names mislead
    (r"CHANCE_OF_VOMIT_AGENT", "Extra vomit chance at full infection", "%"),
    (r"CHANCE_OF_VOMIT", "Vomit chance per check", "%"),
    (r"WATER_LOSS_\w*_MIN", "Water loss floor, share of full", "×"),
    (r"WATER_LOSS_\w+", "Water loss at full infection", "ml/s"),
    (r"STOMACH_MIN_VOLUME", "Vomits only with stomach above", "ml"),
    (r"WATER_DRAIN_FROM_VOMIT", "Water lost per vomit", "ml"),
    (r"ENERGY_DRAIN_FROM_VOMIT", "Energy lost per vomit", "kcal"),
]


CONST_HIDDEN = r"RND_DIVIDER|DEBUG|_ID$"  # internal tuning, not useful to players


def const_unit(name):
    return next((u for key, u in CONST_UNITS if key in name), "")


def const_row(name, value):
    for rx, label, unit in CONST_LABELS:
        if re.fullmatch(rx, name):
            return (label, value, unit)
    unit = const_unit(name)
    if unit == "HP/s" and isinstance(value, (int, float)):
        value = abs(value)  # some scripts store damage as a negative delta
    return (humanize_const(name), value, unit)


def label_range(names):
    if not names:
        return "Pristine"
    return names[0] if len(names) == 1 else f"{names[0]} to {names[-1]}"


def recoil_view(r, all_recoil):
    pts = r.get("curve", [])
    max_dist = max((x.get("MouseOffsetDistance") or 0) for x in all_recoil.values()) or 1
    return {
        "kick": r.get("MouseOffsetDistance"), "kickRel": (r.get("MouseOffsetDistance") or 0) / max_dist,
        "angle": [r.get("MouseOffsetRangeMin"), r.get("MouseOffsetRangeMax")],
        "camera": r.get("CamOffsetDistance"), "curve": pts,
        "fan": fan_path(r.get("MouseOffsetRangeMin"), r.get("MouseOffsetRangeMax"), (r.get("MouseOffsetDistance") or 0) / max_dist),
        "path": curve_paths(pts),
    }


def fan_path(a0, a1, rel, cx=60, cy=64, R=56):
    if a0 is None or a1 is None:
        return None
    r = max(10, R * rel)
    p = lambda a: (cx + r * math.cos(math.radians(a)), cy - r * math.sin(math.radians(a)))
    (x0, y0), (x1, y1) = p(a0), p(a1)
    large = 1 if abs(a1 - a0) > 180 else 0
    return {"d": f"M{cx},{cy} L{x0:.1f},{y0:.1f} A{r:.1f},{r:.1f} 0 {large} 0 {x1:.1f},{y1:.1f} Z",
            "mid": p((a0 + a1) / 2)}


def curve_paths(pts, w=160, h=110, samples=5):
    """Hands recoil spline sampled across its random horizontal range, as smooth paths."""
    if not pts:
        return None
    xs = [abs(v) for p in pts for v in p["x"]] or [1]
    ys = [p["y"] for p in pts] or [1]
    sx, sy = (w / 2 - 8) / (max(xs) or 1), (h - 12) / (max(ys) or 1)

    def smooth(ps):  # Catmull-Rom through the points, as cubic Beziers
        d = f"M{ps[0][0]:.1f},{ps[0][1]:.1f}"
        for i in range(len(ps) - 1):
            p0, p1, p2 = ps[max(i - 1, 0)], ps[i], ps[i + 1]
            p3 = ps[min(i + 2, len(ps) - 1)]
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
        return d

    paths = []
    for k in range(samples):
        t = k / (samples - 1)
        ps = [(w / 2, h - 4)] + [(w / 2 + (p["x"][0] + (p["x"][1] - p["x"][0]) * t) * sx, h - 4 - p["y"] * sy) for p in pts] + [(w / 2, h - 4)]
        paths.append(smooth(ps))
    return {"paths": paths, "mid": paths[samples // 2], "w": w, "h": h}


def specs(c, cat=None):
    if not c:
        return None
    rows = []
    add = lambda label, val: val not in (None, "", [], 0) and rows.append((label, val))
    add("Weight", f"{c['weight'] / 1000:g} kg" if isinstance(c.get("weight"), (int, float)) and c.get("weight") >= 1000 else
        f"{c['weight']:g} g" if isinstance(c.get("weight"), (int, float)) and c.get("weight") else None)
    if isinstance(c.get("itemSize"), list) and cat != "vehicles":
        add("Size", "×".join(str(x) for x in c["itemSize"]) + " slots")
    if isinstance(c.get("itemsCargoSize"), list) and any(c["itemsCargoSize"]):
        w, h = c["itemsCargoSize"][:2]
        add("Storage", f"{w * h} slots ({w}×{h})")
    add("Capacity", c.get("count") and f"{c['count']} rounds")
    add("Muzzle velocity", c.get("initSpeed") and f"{c['initSpeed']} m/s")
    if cat == "clothing" and isinstance(c.get("heatIsolation"), (int, float)):
        add("Insulation", f"{round(c['heatIsolation'] * 100)}%")
    if cat == "clothing" and isinstance(c.get("visibilityModifier"), (int, float)) and c["visibilityModifier"] < 1:
        add("Visibility", f"{round(c['visibilityModifier'] * 100)}%")
    hp = dig(c, ("DamageSystem", "GlobalHealth", "Health", "hitpoints"))
    add("Durability", hp and f"{hp:g} HP")
    return rows or None


def media(cat, cls):
    out = {}
    for kind, rel in (("image", f"media/renders/{cat}/{cls}.webp"), ("model", f"media/models/{cls}.glb")):
        if os.path.exists(os.path.join(ROOT, rel)):
            out[kind] = "/" + rel
    return out


def media_sound(cls, sample_path):
    name = os.path.basename(sample_path)
    rel = f"media/sounds/{cls}/{name}.ogg"
    return "/" + rel if os.path.exists(os.path.join(ROOT, rel)) else None


def humanize_const(name):
    words = name.lower().split("_")
    return " ".join(words).capitalize()


def recipe_title(r):
    n = r.get("name") or ""
    if n and not n.startswith("#"):
        return n
    base = re.sub(r"^(Craft|Prepare)", "", r["class"])
    return humanize(base).strip().capitalize() or r["class"]


def fmt_num(v, digits=2):
    if v is None or isinstance(v, Undefined):
        return "–"
    if isinstance(v, bool):
        return "On" if v else "Off"
    if isinstance(v, float):
        return f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return f"{v:,}"


def fmt_unit(v, unit):
    if isinstance(v, bool) or v is None:
        return fmt_num(v)
    if unit == "dur":
        return duration(v)
    if unit == "/h":
        return f"{fmt_num(v, 1)} per hour"
    if unit in ("%", "°C", "ml", "ml/s", "kcal", "HP", "HP/s", "agents", "kg", "m", "m/s"):
        return f"{fmt_num(v, 3)} {unit}" if unit != "%" else f"{fmt_num(v, 3)}%"
    if unit == "×":
        return "Free" if v == 0 else f"×{fmt_num(v)}"
    if unit == "s":
        return "None" if v == 0 else f"{fmt_num(v)} s"
    if unit == "g":
        return f"{v / 1000:g} kg"
    return fmt_num(v)


def delta(new, old):
    if new is None or old in (None, 0) or new == old:
        return ""
    pct = (new - old) / old * 100
    return f"{'+' if pct > 0 else ''}{pct:.0f}%"


def duration(sec):
    if sec is None:
        return "–"
    sec = int(sec)
    if sec >= 86400 and sec % 3600 == 0:
        return f"{sec / 86400:g} d"
    if sec >= 3600:
        return f"{round(sec / 3600, 1):g} h"
    if sec >= 60:
        return f"{round(sec / 60, 1):g} min"
    return f"{sec} s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--base", default="", help="URL prefix when the site is served from a subpath, e.g. /PureZ-wiki")
    a = ap.parse_args()
    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "templates")), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters.update(num=fmt_num, delta=delta, duration=duration, humanize=humanize, unit=fmt_unit,
                       pct=lambda v, d=0: "–" if v is None else f"{v * 100:.{d}f}%")
    w = Wiki(a.preview, a.base)
    w.collect()
    n = w.render(env)
    print(f"built {n} pages -> {OUT}")


if __name__ == "__main__":
    main()
