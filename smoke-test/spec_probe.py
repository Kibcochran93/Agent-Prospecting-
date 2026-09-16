import json

spec = json.load(open("smoke-test/apollo-openapi.json", encoding="utf-8"))


def resolve(node):
    while isinstance(node, dict) and "$ref" in node:
        node = spec["components"]["schemas"][node["$ref"].split("/")[-1]]
    return node


op = spec["paths"]["/sequences"]["post"]
body = resolve(op["requestBody"]["content"]["application/json"]["schema"])
props = body.get("properties", {})
print("POST /sequences top-level:", sorted(props))
print("required:", body.get("required"))

steps = resolve(props.get("emailer_steps", {}))
item = resolve(steps.get("items", {}))
print("\nstep properties:")
for name, value in (item.get("properties") or {}).items():
    v = resolve(value)
    print(f"  {name}: type={v.get('type')} enum={v.get('enum')}")
print("step required:", item.get("required"))

touches = resolve((item.get("properties") or {}).get("emailer_touches", {}))
titem = resolve(touches.get("items", {}))
print("\ntouch properties:")
for name, value in (titem.get("properties") or {}).items():
    v = resolve(value)
    print(f"  {name}: type={v.get('type')} enum={v.get('enum')}")
print("touch required:", titem.get("required"))

ex = op["requestBody"]["content"]["application/json"].get("examples") or {}
for key, val in list(ex.items())[:2]:
    print("\nexample", key, json.dumps(val.get("value"), indent=1)[:1200])
