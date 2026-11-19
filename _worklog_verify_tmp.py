"""Temporary: print the filled rows of the new workbook so they can be checked."""
import re
import zipfile
from datetime import date, timedelta
from html import unescape

PATH = r"C:\Users\user\Desktop\Internshi report\AI_ML_Internship_Daily_Work_Log_Updated_19_Nov_2026.xlsx"

with zipfile.ZipFile(PATH) as z:
    for part in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"):
        xml = z.read(part).decode("utf-8")
        is_log = part.endswith("sheet1.xml")
        print("=====", part)
        for row in re.finditer(r'<x:row r="(\d+)"([^>]*)>(.*?)</x:row>', xml, re.S):
            r = int(row.group(1))
            if is_log and not 15 <= r <= 43:
                continue
            vals = {}
            for col, body in re.findall(r'<x:c r="([A-Z]+)\d+"[^>]*?(?:/>|>(.*?)</x:c>)', row.group(3), re.S):
                v = re.search(r"<x:v>(.*?)</x:v>", body or "", re.S)
                if v:
                    vals[col] = unescape(v.group(1))
            if is_log and "A" in vals:
                vals["A"] = (date(1899, 12, 30) + timedelta(days=int(vals["A"]))).strftime("%d %b")
            ht = re.search(r'ht="([^"]+)"', row.group(2))
            print(f"row {r} ht={ht.group(1) if ht else '-'} :: " + " | ".join(f"{k}: {v}" for k, v in vals.items()))
