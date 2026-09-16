import json

spec = json.load(open("smoke-test/apollo-openapi.json", encoding="utf-8"))


def resolve(node):
    while isinstance(node, dict) and "$ref" in node:
        node = spec["components"]["schemas"][node["$ref"].split("/")[-1]]
    return node


for path in ("/sequences/{id}", "/sequences"):
    print("==", path, "methods:", sorted(spec["paths"].get(path, {})))
    for method, op in spec["paths"].get(path, {}).items():
        if method in ("get", "put", "patch", "post"):
            print(f"   {method.upper()}: {op.get('summary')}")
            rb = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
            if rb:
                props = sorted((resolve(rb).get("properties") or {}))
                print("     body fields:", props)

# folders, for grouping
folders = [p for p in spec["paths"] if "folder" in p]
print("\nfolder paths:", folders)
for p in folders:
    for m, op in spec["paths"][p].items():
        if m in ("post", "get"):
            print(f"  {m.upper()} {p}: {op.get('summary')}")
