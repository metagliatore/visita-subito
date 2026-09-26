import json
import re
from pathlib import Path

DIR = Path("data/study/providers")

def analyze_html(path: Path):
    html = path.read_text(encoding="utf-8")
    
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = title_m.group(1).strip() if title_m else ""
    
    forms = []
    form_matches = re.finditer(r"<form\b([^>]*)>(.*?)</form>", html, re.I | re.S)
    for fm in form_matches:
        form_attrs_str = fm.group(1)
        form_body = fm.group(2)
        
        def attr(name, s):
            m = re.search(r'\b' + name + r'=(?:["\']([^"\']*)["\']|([^\s>]+))', s, re.I)
            if m:
                return m.group(1) or m.group(2)
            return None

        action = attr("action", form_attrs_str)
        method = (attr("method", form_attrs_str) or "GET").upper()
        fid = attr("id", form_attrs_str)
        fname = attr("name", form_attrs_str)
        
        inputs = []
        for im in re.finditer(r"<input\b([^>]*)>", form_body, re.I):
            istr = im.group(1)
            inputs.append({
                "id": attr("id", istr),
                "name": attr("name", istr),
                "type": (attr("type", istr) or "text").lower(),
                "placeholder": attr("placeholder", istr),
                "value": attr("value", istr) if (attr("type", istr) or "").lower() in ("hidden", "submit") else None,
            })
            
        buttons = []
        for bm in re.finditer(r"<button\b([^>]*)>(.*?)</button>", form_body, re.I | re.S):
            bstr = bm.group(1)
            btxt = re.sub(r"<[^>]+>", "", bm.group(2)).strip()
            buttons.append({
                "id": attr("id", bstr),
                "name": attr("name", bstr),
                "type": (attr("type", bstr) or "button").lower(),
                "text": btxt,
                "onclick": attr("onclick", bstr),
                "class": attr("class", bstr)
            })
            
        forms.append({
            "id": fid,
            "name": fname,
            "action": action,
            "method": method,
            "inputs": inputs,
            "buttons": buttons
        })
        
    return {
        "file": path.name,
        "title": title,
        "forms": forms,
    }

def main():
    files = [
        "cie_real_landing.html",
        "spid_poste.html",
        "spid_aruba.html",
        "spid_infocert.html",
        "spid_lepida.html",
        "spid_namirial.html"
    ]
    for f in files:
        p = DIR / f
        if p.exists():
            data = analyze_html(p)
            out_p = DIR / f.replace(".html", "_fields.json")
            out_p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"=== {f} ({data['title']}) ===")
            print(f"Forms count: {len(data['forms'])}")
            for form in data['forms']:
                print(f"  Form action={form['action']} method={form['method']} id={form['id']} name={form['name']}")
                for inp in form['inputs']:
                    if inp['type'] != 'hidden':
                        print(f"    Input: id={inp['id']} name={inp['name']} type={inp['type']} ph={inp['placeholder']}")
                for btn in form['buttons']:
                    print(f"    Button: id={btn['id']} name={btn['name']} text='{btn['text']}' onclick={btn['onclick']}")
            print()

if __name__ == "__main__":
    main()
