#!/usr/bin/env python3
import csv, io, re, time, unicodedata
from pathlib import Path
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "personalities.csv"
OUT_DIR = ROOT / "personalities"
MANIFEST = ROOT / "manifest.csv"
FAILED = ROOT / "failed.csv"
RAW_BASE = "https://raw.githubusercontent.com/samfoufun-ai/space-monkeyz-images/main/personalities"

UA = "SpaceMonkeyzImageBuilder/3.0 (educational project; contact: samfoufun-ai)"
session = requests.Session()
retry = Retry(
    total=6, connect=5, read=5, status=6,
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    respect_retry_after_header=True,
)
session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4))
session.headers.update({"User-Agent": UA, "Accept-Language":"fr,en;q=0.8"})

ALIASES = {
    "Sting": "Sting (musician)",
    "Prince": "Prince (musician)",
    "Drake": "Drake (musician)",
    "Michou": "Michou (YouTuber)",
    "Pelé": "Pelé",
    "Neymar": "Neymar",
    "Bono": "Bono",
    "Sia": "Sia",
    "Banksy": "Banksy",
    "Molière": "Molière",
    "HugoDécrypte": "HugoDécrypte",
    "Inoxtag": "Inoxtag",
    "Natoo": "Natoo",
}

def norm(s):
    s=unicodedata.normalize("NFKD", str(s or ""))
    s="".join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+"," ",s).strip()

def slugify(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "personality"

def api_json(url, params, timeout=35):
    r=session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    time.sleep(0.12)
    return r.json()

def wikipedia_search(name, lang):
    q=ALIASES.get(name,name)
    params={
        "action":"query","format":"json","formatversion":"2",
        "generator":"search","gsrsearch":q,"gsrlimit":"6",
        "prop":"pageimages|info","piprop":"thumbnail|original",
        "pithumbsize":"700","inprop":"url"
    }
    data=api_json(f"https://{lang}.wikipedia.org/w/api.php",params)
    pages=(data.get("query") or {}).get("pages") or []
    if not pages:return None

    def score(p):
        t=p.get("title","")
        n1,n2=norm(q),norm(t)
        exact=1 if n1==n2 else 0
        contains=1 if n1 and (n1 in n2 or n2 in n1) else 0
        has_img=1 if (p.get("thumbnail") or p.get("original")) else 0
        return (exact,contains,has_img,-len(t))

    pages.sort(key=score, reverse=True)
    for p in pages:
        src=(p.get("thumbnail") or p.get("original") or {}).get("source")
        if not src:continue
        title=p.get("title",q)
        return {
            "source_image":src,
            "resolved_title":title,
            "source_page":p.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ','_'))}",
            "source_kind":f"wikipedia-{lang}",
        }
    return None

def wikidata_image(name, lang):
    q=ALIASES.get(name,name)
    search=api_json("https://www.wikidata.org/w/api.php",{
        "action":"wbsearchentities","format":"json","language":lang,
        "uselang":lang,"type":"item","limit":"8","search":q
    })
    hits=search.get("search") or []
    if not hits:return None

    nq=norm(q)
    hits.sort(key=lambda h:(norm(h.get("label",""))==nq,
                            nq in norm(h.get("label","")) or norm(h.get("label","")) in nq), reverse=True)
    ids=[h.get("id") for h in hits[:5] if h.get("id")]
    if not ids:return None
    ent=api_json("https://www.wikidata.org/w/api.php",{
        "action":"wbgetentities","format":"json","ids":"|".join(ids),"props":"claims|labels|sitelinks",
        "languages":"fr|en"
    }).get("entities") or {}

    for qid in ids:
        e=ent.get(qid) or {}
        claims=e.get("claims") or {}
        p18=claims.get("P18") or []
        if not p18:continue
        try:
            filename=p18[0]["mainsnak"]["datavalue"]["value"]
        except Exception:
            continue
        commons=api_json("https://commons.wikimedia.org/w/api.php",{
            "action":"query","format":"json","formatversion":"2","titles":"File:"+filename,
            "prop":"imageinfo","iiprop":"url","iiurlwidth":"700"
        })
        pages=(commons.get("query") or {}).get("pages") or []
        if not pages:continue
        ii=(pages[0].get("imageinfo") or [])
        if not ii:continue
        src=ii[0].get("thumburl") or ii[0].get("url")
        if not src:continue
        label=((e.get("labels") or {}).get(lang) or (e.get("labels") or {}).get("en") or {}).get("value",q)
        page=((e.get("sitelinks") or {}).get("frwiki") or (e.get("sitelinks") or {}).get("enwiki") or {}).get("title")
        source_page=f"https://www.wikidata.org/wiki/{qid}"
        if page:
            wiki_lang="fr" if (e.get("sitelinks") or {}).get("frwiki") else "en"
            source_page=f"https://{wiki_lang}.wikipedia.org/wiki/{quote(page.replace(' ','_'))}"
        return {
            "source_image":src,"resolved_title":label,
            "source_page":source_page,"source_kind":"wikidata-p18"
        }
    return None

def commons_search(name):
    # Last fallback: search Commons files by person's full name.
    q=ALIASES.get(name,name)
    data=api_json("https://commons.wikimedia.org/w/api.php",{
        "action":"query","format":"json","formatversion":"2",
        "generator":"search","gsrsearch":q,"gsrnamespace":"6","gsrlimit":"12",
        "prop":"imageinfo","iiprop":"url|extmetadata","iiurlwidth":"700"
    })
    pages=(data.get("query") or {}).get("pages") or []
    nq=norm(q)
    ranked=[]
    for p in pages:
        title=p.get("title","")
        ii=(p.get("imageinfo") or [])
        if not ii:continue
        src=ii[0].get("thumburl") or ii[0].get("url")
        if not src:continue
        score=(1 if nq in norm(title) else 0, -len(title))
        ranked.append((score,p,src))
    if not ranked:return None
    ranked.sort(key=lambda x:x[0],reverse=True)
    _,p,src=ranked[0]
    return {
        "source_image":src,"resolved_title":p.get("title",q),
        "source_page":"https://commons.wikimedia.org/wiki/"+quote(p.get("title","").replace(" ","_")),
        "source_kind":"commons-search"
    }

def resolve(name):
    for fn,args in [
        (wikipedia_search,(name,"fr")),
        (wikipedia_search,(name,"en")),
        (wikidata_image,(name,"fr")),
        (wikidata_image,(name,"en")),
        (commons_search,(name,))
    ]:
        try:
            hit=fn(*args)
            if hit:return hit
        except Exception as e:
            print(f"  fallback warning {name}: {type(e).__name__}: {e}")
        time.sleep(0.15)
    return None

def download_as_jpg(url,dest):
    r=session.get(url,timeout=60)
    r.raise_for_status()
    img=Image.open(io.BytesIO(r.content))
    img=ImageOps.exif_transpose(img).convert("RGB")
    # Standard portrait asset for Excel + Space Monkeyz.
    img.thumbnail((700,700),Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True,exist_ok=True)
    img.save(dest,"JPEG",quality=86,optimize=True,progressive=True)

def load_old_manifest():
    out={}
    if not MANIFEST.exists():return out
    try:
        with MANIFEST.open("r",encoding="utf-8-sig",newline="") as f:
            for r in csv.DictReader(f):
                if r.get("name"):out[r["name"]]=r
    except Exception:
        pass
    return out

def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    with CSV_PATH.open("r",encoding="utf-8-sig",newline="") as f:
        rows=list(csv.DictReader(f))
    old=load_old_manifest()
    manifest=[];failed=[]

    for i,row in enumerate(rows,1):
        name=row["name"].strip();category=row["category"].strip()
        filename=slugify(name)+".jpg";dest=OUT_DIR/filename

        # Keep already generated good files, avoiding needless Wikimedia requests.
        oldrow=old.get(name) or {}
        if dest.exists() and dest.stat().st_size>3000 and oldrow.get("status")=="ok":
            manifest.append(oldrow)
            print(f"[{i:03d}/{len(rows)}] KEEP {name}")
            continue

        hit=resolve(name)
        if not hit:
            err="aucune image trouvée après Wikipedia FR/EN + Wikidata P18 + Commons"
            failed.append({"id":row["id"],"name":name,"category":category,"error":err})
            manifest.append({
                "id":row["id"],"name":name,"category":category,"filename":"","raw_url":"",
                "source_page":"","source_image":"","source_kind":"","resolved_title":"","status":"failed"
            })
            print(f"[{i:03d}/{len(rows)}] MISS {name}")
            continue

        try:
            download_as_jpg(hit["source_image"],dest)
            raw_url=f"{RAW_BASE}/{filename}"
            manifest.append({
                "id":row["id"],"name":name,"category":category,"filename":filename,"raw_url":raw_url,
                "source_page":hit["source_page"],"source_image":hit["source_image"],
                "source_kind":hit["source_kind"],"resolved_title":hit["resolved_title"],"status":"ok"
            })
            print(f"[{i:03d}/{len(rows)}] OK   {name} <- {hit['source_kind']} / {hit['resolved_title']}")
        except Exception as e:
            err=f"échec téléchargement/conversion: {e}"
            failed.append({"id":row["id"],"name":name,"category":category,"error":err})
            manifest.append({
                "id":row["id"],"name":name,"category":category,"filename":"","raw_url":"",
                "source_page":hit["source_page"],"source_image":hit["source_image"],
                "source_kind":hit["source_kind"],"resolved_title":hit["resolved_title"],"status":"failed"
            })
            print(f"[{i:03d}/{len(rows)}] ERR  {name}: {e}")
        time.sleep(0.12)

    fields=["id","name","category","filename","raw_url","source_page","source_image","source_kind","resolved_title","status"]
    with MANIFEST.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(manifest)
    with FAILED.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["id","name","category","error"]);w.writeheader();w.writerows(failed)
    ok=sum(1 for r in manifest if r.get("status")=="ok")
    print(f"Done: {ok}/{len(manifest)} ok, {len(failed)} failed")

if __name__=="__main__":
    main()
