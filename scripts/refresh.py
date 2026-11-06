"""Weekly card-data refresh for the site.
Downloads Scryfall's card data and Commander Spellbook's combos, trims them to what the site uses,
and rewrites cards.json, aliases.json, combos.json and meta.json. If a source fails, the old file is kept
(and combos are re-linked to the new card list by name, so they never point at the wrong card)."""
import json, sys, os, datetime, gzip, io, urllib.request, ijson

UA = {"User-Agent": "dvl-deck-refresh/1.0", "Accept": "application/json"}
FMT = ["standard", "pioneer", "modern", "legacy", "vintage", "commander", "pauper", "brawl"]
SKIP_LAYOUT = {"token", "double_faced_token", "emblem", "art_series", "planar", "scheme", "vanguard", "augment", "host", "reversible_card"}

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600)

def bulk_items(kind):
    """Yield every card object from one of Scryfall's bulk files (JSON Lines or classic JSON array)."""
    meta = json.load(get("https://api.scryfall.com/bulk-data"))
    items = meta.get("data", meta if isinstance(meta, list) else [])
    b = next((x for x in items if x.get("type") == kind), None)
    if b is None: raise RuntimeError(f"no bulk entry '{kind}'; saw {[x.get('type') for x in items]}")
    if not (b.get("jsonl_download_uri") or b.get("download_uri")) and b.get("uri"): b = {**b, **json.load(get(b["uri"]))}
    url, lines = (b["jsonl_download_uri"], True) if b.get("jsonl_download_uri") else (b.get("download_uri"), False)
    if not url: raise RuntimeError(f"bulk entry '{kind}' has no download link; keys: {sorted(b.keys())}")
    raw = get(url); head = raw.read(2); stream = io.BufferedReader(_Prefixed(head, raw))
    if head == b"\x1f\x8b": stream = gzip.GzipFile(fileobj=stream)
    if lines:
        for ln in io.TextIOWrapper(stream, encoding="utf-8"):
            ln = ln.strip().rstrip(",")
            if ln and ln not in ("[", "]"): yield json.loads(ln)
    else:
        yield from ijson.items(stream, "item", use_float=True)

class _Prefixed(io.RawIOBase):
    def __init__(self, head, rest): self.head, self.rest = head, rest
    def readable(self): return True
    def readinto(self, b):
        if self.head:
            n = min(len(b), len(self.head)); b[:n] = self.head[:n]; self.head = self.head[n:]; return n
        data = self.rest.read(len(b)); n = len(data); b[:n] = data; return n

def trim_card(c):
    if c.get("layout") in SKIP_LAYOUT: return None
    leg_any = any(c["legalities"].get(f) in ("legal", "restricted", "banned") for f in FMT)
    # Scryfall picks one printing per card; if that printing is digital, the card can still exist in paper.
    if not leg_any and ("paper" not in c.get("games", []) or c.get("set_type") in ("memorabilia", "token")): return None
    faces = c.get("card_faces")
    def g(k):
        if c.get(k) not in (None, ""): return c[k]
        return faces[0].get(k, "") if faces else ""
    if faces and not c.get("oracle_text"):
        text = "\n//\n".join(f"{f.get('name', '')}: " * (i > 0) + f.get("oracle_text", "") for i, f in enumerate(faces))
        typ = " // ".join(f.get("type_line", "") for f in faces); cost = faces[0].get("mana_cost", "")
    else:
        text = c.get("oracle_text", ""); typ = c.get("type_line", ""); cost = c.get("mana_cost", "")
    leg = 0
    for i, f in enumerate(FMT):
        if c["legalities"].get(f) in ("legal", "restricted"): leg |= 1 << i
    colors = c.get("colors") or (faces[0].get("colors") if faces else []) or []
    p = c.get("prices") or {}; price = p.get("usd") or p.get("usd_foil") or ""
    return [c["name"], cost, float(c.get("cmc", 0) or 0), typ, text, "".join(colors), "".join(c.get("color_identity", [])),
            g("power"), g("toughness"), g("loyalty"), (c.get("rarity") or "c")[0], c.get("set", ""), leg,
            c.get("edhrec_rank") or 0, float(price) if price else 0, 1 if c.get("game_changer") else 0, (c.get("released_at") or "")[:4], c.get("id", "")]

def build_cards():
    out = []
    for c in bulk_items("oracle_cards"):
        r = trim_card(c)
        if r: out.append(r)
    out.sort(key=lambda r: (r[13] == 0, r[13], r[0]))
    if len(out) < 25000: raise RuntimeError(f"only {len(out)} cards; refusing to overwrite")
    return {"fields": ["name", "cost", "cmc", "type", "text", "colors", "ci", "pow", "tou", "loy", "rar", "set", "leg", "rank", "usd", "gc", "year", "img"], "formats": FMT, "cards": out}

def build_aliases(names):
    al = {}
    for c in bulk_items("default_cards"):
        fn = c.get("flavor_name") or (c.get("card_faces") or [{}])[0].get("flavor_name")
        if fn and c.get("name") in names and fn.lower() not in names: al[fn] = c["name"]
    if len(al) < 100: raise RuntimeError("too few aliases")
    return dict(sorted(al.items()))

def build_combos(index):
    feats, fidx, out = [], {}, []
    src = get("https://json.commanderspellbook.com/variants.json")
    for v in ijson.items(src, "variants.item"):
        if v.get("status") not in ("OK", "E", None): continue
        uses = v.get("uses") or []
        ids = [index.get(((u.get("card") or {}).get("name") or "").lower()) for u in uses]
        if not ids or any(i is None for i in ids): continue
        fs = []
        for pr in v.get("produces") or []:
            nm = ((pr.get("feature") or {}).get("name") or "").strip()
            if not nm: continue
            if nm not in fidx: fidx[nm] = len(feats); feats.append(nm)
            fs.append(fidx[nm])
        cmd = [index[((u.get("card") or {}).get("name") or "").lower()] for u in uses if u.get("mustBeCommander")]
        out.append([ids, fs[:4], int(v.get("popularity") or 0), str(v.get("id", "")), len(v.get("requires") or []), str(v.get("bracketTag") or ""), cmd])
    if len(out) < 20000: raise RuntimeError(f"only {len(out)} combos")
    out.sort(key=lambda x: -x[2])
    return {"features": feats, "combos": out}

def relink_combos(old_cards, new_index):
    """Keep last week's combos but point them at the new card list."""
    old = json.load(open("combos.json")); names = [r[0] for r in old_cards["cards"]]; keep = []
    for cb in old["combos"]:
        m = [new_index.get(names[i].lower()) if i < len(names) else None for i in cb[0]]
        if all(x is not None for x in m):
            cmd = [new_index.get(names[i].lower()) for i in cb[6] if i < len(names)]
            keep.append([m, cb[1], cb[2], cb[3], cb[4], cb[5], [x for x in cmd if x is not None]])
    return {"features": old["features"], "combos": keep}

def dump(obj, path):
    tmp = path + ".tmp"; json.dump(obj, open(tmp, "w"), separators=(",", ":"), ensure_ascii=False); os.replace(tmp, path)

def main():
    old_cards = json.load(open("cards.json"))
    try: cards = build_cards()
    except Exception as e: print("Card data failed, keeping everything as is:", e); return 0
    index = {r[0].lower(): i for i, r in enumerate(cards["cards"])}
    try: combos = build_combos(index); print("combos:", len(combos["combos"]))
    except Exception as e: print("Combo data failed, re-linking last week's combos:", e); combos = relink_combos(old_cards, index)
    try: aliases = build_aliases({r[0] for r in cards["cards"]} | set(index)); print("aliases:", len(aliases))
    except Exception as e: print("Alias data failed, keeping old aliases:", e); aliases = json.load(open("aliases.json"))
    dump(cards, "cards.json"); dump(combos, "combos.json"); dump(aliases, "aliases.json")
    dump({"pulled": datetime.date.today().isoformat(), "cards": len(cards["cards"]), "combos": len(combos["combos"])}, "meta.json")
    print("cards:", len(cards["cards"]))
    return 0

if __name__ == "__main__": sys.exit(main())
