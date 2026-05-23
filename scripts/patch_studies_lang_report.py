#!/usr/bin/env python3
"""
Patch app/routers/studies.py to accept `lang` and `report_type` form fields.

After this patch:
- POST /api/v1/studies accepts optional `lang` (default 'en') and
  `report_type` (default 'clinical') form fields.
- Both are threaded through to ai_client.analyze_image() via the meta dict.

This expects ai_client.analyze_image() to forward `lang` and `report_type`
to the Railway AI service's /predict_with_report endpoint. If your
ai_client.py uses meta["lang"] already, no changes to that file are needed.
If it doesn't forward report_type, a second patch on ai_client.py is needed
— this script prints a check at the end so you know.

Run from your scanguru-portal repo root:

    python3 scripts/patch_studies_lang_report.py

It backs up studies.py to studies_backup_<timestamp>.py before editing.
"""
import os, re, shutil, sys
from datetime import datetime

TARGET = "app/routers/studies.py"
AI_CLIENT = "app/ai_client.py"

if not os.path.exists(TARGET):
    sys.exit(f"ERROR: {TARGET} not found. Run this from your scanguru-portal repo root.")

backup = f"app/routers/studies_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.py"
shutil.copy(TARGET, backup)
print(f"Backed up to {backup}")

with open(TARGET) as f:
    src = f.read()

# --- 1) Add lang + report_type as Form fields to create_study() signature ---
# Find:  urgency: Urgency = Form(Urgency.routine),
# Add right after it:  lang: str = Form("en"),  report_type: str = Form("clinical"),
if "lang: str = Form(" in src:
    print("lang already present — skipping signature patch")
else:
    src, n = re.subn(
        r'(    urgency: Urgency = Form\(Urgency\.routine\),\n)',
        r'\1    lang: str = Form("en"),\n    report_type: str = Form("clinical"),\n',
        src,
    )
    if n != 1:
        sys.exit(f"ERROR: could not find urgency Form param in create_study signature. Backup at {backup}.")
    print("Added lang + report_type to create_study() signature")

# --- 2) Replace the meta dict so it uses the user-supplied lang + report_type ---
old_meta = 'meta = {"name": name, "age": age, "sex": sex, "lang": "en"}'
new_meta = 'meta = {"name": name, "age": age, "sex": sex, "lang": lang, "report_type": report_type}'
if new_meta in src:
    print("meta dict already updated — skipping")
elif old_meta in src:
    src = src.replace(old_meta, new_meta)
    print("Updated meta dict to forward lang + report_type")
else:
    print("WARNING: could not find the original meta dict line — please add manually:")
    print(f"   {new_meta}")

with open(TARGET, "w") as f:
    f.write(src)

print(f"\nPatched {TARGET}.\n")

# --- 3) Sanity check ai_client.py forwards both fields ---
if os.path.exists(AI_CLIENT):
    with open(AI_CLIENT) as f:
        ai = f.read()
    has_lang = "lang" in ai
    has_report = "report_type" in ai
    print("Checking ai_client.py:")
    print(f"  forwards 'lang'        : {'YES' if has_lang else 'NO — needs patching'}")
    print(f"  forwards 'report_type' : {'YES' if has_report else 'NO — needs patching'}")
    if not (has_lang and has_report):
        print()
        print("  ai_client.analyze_image(image_bytes, filename, modality, meta) needs to")
        print("  forward meta['lang'] and meta['report_type'] to the Railway AI service.")
        print("  Look in app/ai_client.py for the POST to /predict_with_report and ensure")
        print("  both are sent as form fields (or as query params on the URL).")
        print()
        print("  Suggested ai_client pattern (paste/adapt into the analyze_image function):")
        print('     data = {')
        print('         "modality": modality,')
        print('         "consent": "true",')
        print('         "name": meta.get("name") or "",')
        print('         "age": meta.get("age") or "",')
        print('         "sex": meta.get("sex") or "",')
        print('         "lang": meta.get("lang") or "en",')
        print('         "report_type": meta.get("report_type") or "clinical",')
        print('     }')
        print('     url = f"{AI_SERVICE_URL}/predict_with_report?lang={data[\'lang\']}&report_type={data[\'report_type\']}"')

print()
print("Restart uvicorn (Ctrl-C then re-run it) so the change loads.")
