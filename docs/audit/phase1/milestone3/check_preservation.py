import ast, collections, json
from pathlib import Path
old=Path('../.local/phase1/milestone3-baseline/backend/tests')
new=Path('tests')
def extract(path):
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    return {node.name: collections.Counter(ast.dump(x, include_attributes=False) for x in ast.walk(node) if isinstance(x, ast.Assert)) for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_')}
missing=[]; changed=[]; funcs=assertions=0
for file in old.glob('test_*.py'):
    if not (new/file.name).exists():
        missing.append(str(file.name));continue
    before=extract(file);after=extract(new/file.name)
    for name, checks in before.items():
        funcs+=1;assertions+=sum(checks.values())
        if name not in after: missing.append(file.name+'::'+name)
        elif checks-after[name]: changed.append({'test':file.name+'::'+name,'missing_assertions':sum((checks-after[name]).values())})
result={'original_test_functions':funcs,'original_assertions':assertions,'missing_tests':missing,'missing_assertions':changed}
Path('../.local/phase1/milestone3/m2-tests-preservation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
