import re, glob

files = glob.glob("app/api/*.py")
pattern = re.compile(r"(\.TemplateResponse\(\s*)(\"[^\"]+\.html\")")

for f in files:
    with open(f, "r", encoding="utf-8") as fp:
        content = fp.read()
    new_content = pattern.sub(r"\1request, \2", content)
    if new_content != content:
        with open(f, "w", encoding="utf-8") as fp:
            fp.write(new_content)
        print("Updated:", f)
print("Done")
