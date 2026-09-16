import json

spec = json.load(open("smoke-test/apollo-openapi.json", encoding="utf-8"))


def resolve(node):
    while isinstance(node, dict) and "$ref" in node:
        node = spec["components"]["schemas"][node["$ref"].split("/")[-1]]
    return node


op = spec["paths"]["/sequences"]["post"]
print("security:", op.get("security"))
print("summary:", op.get("summary"))
desc = (op.get("description") or "")[:600]
print("description:", desc)

for code, resp in op.get("responses", {}).items():
    schema = resolve(((resp.get("content") or {}).get("application/json") or {}).get("schema") or {})
    print(f"\nresponse {code}: top-level {sorted((schema.get('properties') or {}))[:12]}")

tmpl = resolve(
    resolve(
        resolve(resolve(op["requestBody"]["content"]["application/json"]["schema"])["properties"]["emailer_steps"])["items"]
    )["properties"]["emailer_touches"]
)
titem = resolve(tmpl["items"])
et = resolve(titem["properties"]["emailer_template"])
print("\nemailer_template props:", sorted((et.get("properties") or {})))
for name, value in (et.get("properties") or {}).items():
    v = resolve(value)
    print(f"  {name}: {v.get('type')}")
