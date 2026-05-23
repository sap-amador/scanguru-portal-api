#!/usr/bin/env python3
"""
Patch app/routers/studies.py to accept an optional `region` form field.

Required for MSK analysis — the AI service needs to know which body region
(hand, finger, forearm, elbow, shoulder, hip, knee, ankle, spine) to use.

After this patch:
- POST /api/v1/studies accepts optional `region` form field.
- Threaded through to ai_client.analyze_image() via the meta dict.

Run from your scanguru-portal repo root:

    python3 scripts/patch_studies_region.py

It backs up studies.py before editing.
"""
import os, re, shutil, sys
from datetime import datetime

TARGET = "app/routers/studies.py"
AI_CLIENT = "app/ai_client.py"

if not os.path.exists(TARGET):
    sys.exit(f"ERROR: {TARGET} not found. Run from your scanguru-portal repo root.")

backup = f"app/routers/studies_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.py"
shutil.copy(TARGET, backup)
print(f"Backed up to {backup}")

with open(TARGET) as f:
    src = f.read()

# --- 1) Add region as a Form field to create_study() signature ---
if "region: Optional[str] = Form(" in src or "region: str = Form(" in src:
    print("region already present in signature — skipping signature patch")
else:
    # Insert region right after report_type (or after urgency if report_type isn't there yet)
    if 'report_type: str = Form("clinical"),' in src:
        src = src.replace(
            '    report_type: str = Form("clinical"),\n',
            '    report_type: str = Form("clinical"),\n    region: Optional[str] = Form(None),\n',
            1,
        )
        print("Added region after report_type")
    elif "    urgency: Urgency = Form(Urgency.routine),\n" in src:
        src = src.replace(
            "    urgency: Urgency = Form(Urgency.routine),\n",
            "    urgency: Urgency = Form(Urgency.routine),\n    region: Optional[str] = Form(None),\n",
            1,
        )
        print("Added region after urgency (report_type not found — apply patch_studies_lang_report.py too)")
    else:
        sys.exit(f"ERROR: could not find an anchor to insert region. Backup at {backup}.")

# --- 2) Update meta dict to include region ---
# Try both new and old forms
new_meta_with_region = '"region": region'
if '"region": region' in src:
    print("region already in meta dict")
else:
    # Most likely current form (after lang patch was applied):
    candidates = [
        ('meta = {"name": name, "age": age, "sex": sex, "lang": lang, "report_type": report_type}',
         'meta = {"name": name, "age": age, "sex": sex, "lang": lang, "report_type": report_type, "region": region}'),
        ('meta = {"name": name, "age": age, "sex": sex, "lang": "en"}',
         'meta = {"name": name, "age": age, "sex": sex, "lang": "en", "region": region}'),
    ]
    matched = False
    for old, new in candidates:
        if old in src:
            src = src.replace(old, new)
            print("Updated meta dict to include region")
            matched = True
            break
    if not matched:
        print("WARNING: could not find meta dict — please add 'region': region manually")

with open(TARGET, "w") as f:
    f.write(src)

print(f"\nPatched {TARGET}.\n")

# --- 3) Sanity check ai_client.py ---
if os.path.exists(AI_CLIENT):
    with open(AI_CLIENT) as f:
        ai = f.read()
    has_region = "region" in ai
    print(f"Checking ai_client.py forwards 'region': {'YES' if has_region else 'NO — needs patching'}")
    if not has_region:
        print()
        print("  ai_client.analyze_image() needs to forward meta['region'] to the AI")
        print("  service as a form field (only when present — MSK only).")
        print()
        print("  Suggested addition inside the form-data builder in analyze_image():")
        print('     if meta.get("region"):')
        print('         data["region"] = meta["region"]')

print("\nRestart uvicorn so the change loads.")
