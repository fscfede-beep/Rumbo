from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/"plugins"/"rumbo-coding-agent-reliability"
MANIFEST=json.loads((PLUGIN/".codex-plugin"/"plugin.json").read_text(encoding="utf-8"))
MARKETPLACE=json.loads((ROOT/".agents"/"plugins"/"marketplace.json").read_text(encoding="utf-8"))

assert MANIFEST["name"]=="rumbo-coding-agent-reliability"
assert MANIFEST["version"]=="0.1.6"
assert MANIFEST["skills"]=="./skills/"
assert MANIFEST["repository"]=="https://github.com/RUMBO-IA/Rumbo"
prompts=MANIFEST["interface"]["defaultPrompt"]
assert 1<=len(prompts)<=3
assert all(isinstance(p,str) and 1<=len(p)<=128 for p in prompts)
assert not MANIFEST.get("apps")
assert not MANIFEST.get("mcpServers")
assert MANIFEST["interface"]["screenshots"]==[]
assert MANIFEST["interface"]["composerIcon"]=="./assets/icon.svg"
assert MANIFEST["interface"]["logo"]=="./assets/logo.svg"
for rel in ("assets/icon.svg","assets/logo.svg"):
    asset=PLUGIN/rel
    assert asset.is_file() and asset.stat().st_size>100
    assert "<svg" in asset.read_text(encoding="utf-8")[:300]

expected={
 "canonical-state-recovery",
 "deep-research-reconcile",
 "goal-loop-controller",
 "execute-verify-close",
 "audit-final-state",
}
skill_names=[p.name for p in (PLUGIN/"skills").iterdir() if p.is_dir()]
assert set(skill_names)==expected
for name in expected:
    text=(PLUGIN/"skills"/name/"SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname:")

for page in ("privacy.html","terms.html","support.html"):
    assert (PLUGIN/page).is_file()
entry=MARKETPLACE["plugins"][0]
assert entry["name"]==MANIFEST["name"]
assert entry["source"]["path"]=="./plugins/rumbo-coding-agent-reliability"
assert entry["policy"] == {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL",
}
# Catalog policy metadata is non-authoritative; workspace settings control installation and authentication.

print("RUMBO_PLUGIN_MANIFEST_PASS")
print("RUMBO_SKILLS_5_PASS")
print("RUMBO_LEGAL_SUPPORT_ASSETS_PASS")
print("RUMBO_MARKETPLACE_PASS")
