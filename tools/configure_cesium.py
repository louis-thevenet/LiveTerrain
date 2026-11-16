#!/usr/bin/env python3
"""Align the official source plugin descriptor with the supported local UE minor.
The v2.31.0 source descriptor targets 5.6.0 even though the release supports 5.8.
No C++ source or global Engine installation is patched.
"""
import argparse
import json
from pathlib import Path
import re

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--engine',type=Path,required=True)
p.add_argument('--plugin',type=Path,required=True)
a = p.parse_args()
v = json.loads((a.engine/'Engine/Build/Build.version').read_text())
if (v['MajorVersion'],v['MinorVersion']) != (5,8):
    p.error('This project pins UE 5.8 and Cesium 2.31.0')
path = a.plugin/'CesiumForUnreal.uplugin'
text = path.read_text()
d = json.loads(text)
if d['VersionName'] != '2.31.0' or d['EngineVersion'] not in ('5.6.0','5.8.0'):
    p.error('Unexpected plugin descriptor; preserve and inspect local changes')
if d['EngineVersion'] != '5.8.0':
    path.write_text(re.sub(r'("EngineVersion"\s*:\s*)"5\.6\.0"',r'\1"5.8.0"',text))
    print('Cesium source descriptor: EngineVersion 5.6.0 -> 5.8.0 (local project only)')
