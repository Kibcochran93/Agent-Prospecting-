import json

spec = json.load(open("smoke-test/apollo-openapi.json", encoding="utf-8"))


def resolve(node):
    while isinstance(node, dict) and "$ref" in node:
        node = spec["components"]["schemas"][node["$ref"].split("/")[-1]]
    return node


op = spec["paths"]["/sequences/{id}"]["put"]
body = resolve(op["requestBody"]["content"]["application/json"]["schema"])
steps = resolve(body["properties"]["emailer_steps"])
item = resolve(steps.get("items", {}))
print("PUT step item props:")
for name, value in (item.get("properties") or {}).items():
    v = resolve(value)
    print(f"  {name}: {v.get('type')}")
print("required:", item.get("required"))

touches = resolve((item.get("properties") or {}).get("emailer_touches", {}))
titem = resolve(touches.get("items", {}))
print("\nPUT touch item props:", sorted((titem.get("properties") or {})))

desc = (op.get("description") or "")
for line in desc.splitlines():
    if any(k in line.lower() for k in ("step", "replace", "existing", "id")):
        print("DESC:", line.strip()[:200])
